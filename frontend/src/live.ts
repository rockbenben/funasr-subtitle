import type { CreateJobOptions } from "./api";
import type { Job, Segment } from "./types";

export type LiveSource = "microphone" | "system";
export interface LiveHandle {
  stop: () => void;
  close: () => void;
}

interface Callbacks {
  onCreated: (job: Job) => void;
  onRecording: () => void;
  onStopping: () => void;
  onChunk: (replaceFrom: number, segments: Segment[]) => void;
  onDone: (job: Job) => void;
  onError: (message: string, detail?: string) => void;
  onNotice: (message: string) => void;
}

/** 必须从点击事件直接调用：共享电脑声音需要浏览器的即时用户手势。 */
export function startLive(source: LiveSource, opts: CreateJobOptions, cb: Callbacks): LiveHandle {
  let stream: MediaStream | null = null;
  let context: AudioContext | null = null;
  let node: AudioWorkletNode | null = null;
  let socket: WebSocket | null = null;
  let closed = false;
  let stopping = false;
  let recording = false;
  let flushed = false;
  let inFlight = false;
  let stopSent = false;
  let flushTimer: number | undefined;
  let connectTimer: number | undefined;
  const pending: ArrayBuffer[] = [];

  const releaseAudio = () => {
    window.clearTimeout(flushTimer);
    stream?.getTracks().forEach((track) => track.stop());
    node?.disconnect();
    if (context) void context.close().catch(() => {});
    stream = null;
    context = null;
  };
  const close = () => {
    closed = true;
    window.clearTimeout(connectTimer);
    releaseAudio();
    pending.length = 0;
    socket?.close();
  };
  const fail = (message: string, detail?: string) => {
    if (closed) return;
    close();
    cb.onError(message, detail);
  };
  const pump = () => {
    if (closed || !socket || socket.readyState !== WebSocket.OPEN || inFlight) return;
    const next = pending.shift();
    if (next) {
      inFlight = true;
      socket.send(next);
    } else if (flushed && !stopSent) {
      stopSent = true;
      socket.send(JSON.stringify({ type: "stop" }));
    }
  };
  const stop = () => {
    if (closed || stopping) return;
    stopping = true;
    cb.onStopping();
    if (!recording || !node) {
      close();
      cb.onError("已取消录音，尚未开始采集声音。");
      return;
    }
    node.port.postMessage("stop");
    flushTimer = window.setTimeout(() => fail("录音收尾超时，已显示的字幕仍可导出。"), 5000);
  };

  void (async () => {
    try {
      if (!navigator.mediaDevices || !window.AudioWorkletNode) {
        throw new Error("当前浏览器不支持录音，请用新版 Chrome 或 Edge 打开本地页面。");
      }
      if (source === "system" && !navigator.mediaDevices.getDisplayMedia) {
        throw new Error("当前浏览器不支持共享电脑声音，请用新版 Chrome 或 Edge 重试。");
      }
      // 先弹浏览器选择器，不能在等待模型或网络之后才申请共享权限。
      stream = source === "system"
        ? await navigator.mediaDevices.getDisplayMedia({ video: true, audio: true })
        : await navigator.mediaDevices.getUserMedia({ audio: true });
      if (closed) { releaseAudio(); return; }
      if (!stream.getAudioTracks().length) {
        throw new Error("没有共享到声音。请重新选择标签页或屏幕，并勾选「共享音频」。");
      }
      context = new AudioContext({ sampleRate: 16000 });
      // 尽早 resume，后续的模型预热可能耗尽用户手势有效期。
      await context.resume();
      await context.audioWorklet.addModule(new URL("pcm-capture.js", document.baseURI).href);
      if (closed) { releaseAudio(); return; }
      if (context.sampleRate !== 16000 || context.state !== "running") {
        throw new Error("浏览器没能启动录音，请用新版 Chrome 或 Edge 重试。");
      }
      node = new AudioWorkletNode(context, "pcm-capture");
      node.onprocessorerror = () => fail("录音中断了，已显示的字幕仍可导出。");
      node.port.onmessage = ({ data }: MessageEvent<ArrayBuffer | string>) => {
        if (closed) return;
        if (data === "stopped") {
          flushed = true;
          releaseAudio();
        } else if (data instanceof ArrayBuffer) {
          pending.push(data);
          // 约 30 秒积压就停止采集并补完，避免慢机器把整堂课堆在内存里。
          if (pending.length >= 50 && !stopping) {
            cb.onNotice("电脑暂时跟不上录音速度，已停止收音，正在补完已录内容。");
            stop();
          }
        }
        pump();
      };
      const scheme = location.protocol === "https:" ? "wss:" : "ws:";
      socket = new WebSocket(`${scheme}//${location.host}/ws/live`);
      connectTimer = window.setTimeout(() => fail("连不上后台，请确认程序还在运行后重试。"), 15000);
      socket.onopen = () => {
        window.clearTimeout(connectTimer);
        socket?.send(JSON.stringify({ source, options: {
          model_id: opts.modelId, language: opts.language, hotwords: opts.hotwords,
          max_chars: opts.maxChars, pause_ms: opts.pauseMs, itn: opts.itn,
          punctuation: opts.punctuation, use_gpu: false, diarization: false,
        } }));
      };
      socket.onmessage = ({ data }) => {
        if (closed) return;
        try {
          const ev = JSON.parse(data);
          if (ev.type === "created") cb.onCreated(ev.job);
          else if (ev.type === "ready") {
            if (!context || !stream || !node) return;
            if (stream.getAudioTracks().some((track) => track.readyState !== "live")) {
              fail("收音已结束，请重新开始。");
              return;
            }
            const audio = context.createMediaStreamSource(new MediaStream(stream.getAudioTracks()));
            audio.connect(node);
            // worklet 输出始终为静音，只让音频图运行，不把麦克风放回扬声器。
            node.connect(context.destination);
            recording = true;
            stream.getTracks().forEach((track) => track.addEventListener("ended", stop, { once: true }));
            cb.onRecording();
          } else if (ev.type === "chunk") {
            inFlight = false;
            cb.onChunk(ev.replace_from, ev.segments);
            pump();
          } else if (ev.type === "done") {
            close();
            cb.onDone(ev.job);
          } else if (ev.type === "error") fail(ev.message, ev.detail);
        } catch (error) {
          fail("读不到转写结果，请停止后重试。", String(error));
        }
      };
      socket.onerror = () => fail("连不上后台了，已显示的字幕仍可导出。");
      socket.onclose = () => fail("转写连接断开了，已显示的字幕仍可导出。");
    } catch (error) {
      const detail = error instanceof Error ? error.message : String(error);
      const denied = error instanceof DOMException && error.name === "NotAllowedError";
      fail(denied ? "未获收音权限或已取消共享，请重新开始并允许收音。"
        : error instanceof DOMException ? "没能打开声音来源，请检查设备和浏览器权限后重试。" : detail, detail);
    }
  })();

  return { stop, close };
}
