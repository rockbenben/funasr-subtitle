// 独立 worklet 文件随便携包静态资源分发，避免录音依赖主线程定时器或外部服务。
class PcmCapture extends AudioWorkletProcessor {
  constructor() {
    super();
    this.buffer = new Int16Array(9600);
    this.used = 0;
    this.stopped = false;
    this.port.onmessage = ({ data }) => {
      if (data === "stop") {
        this.stopped = true;
        if (this.used) this.port.postMessage(this.buffer.slice(0, this.used).buffer);
        this.port.postMessage("stopped");
      }
    };
  }

  process(inputs) {
    if (this.stopped) return false;
    const channels = inputs[0];
    if (!channels?.length) return true;
    for (let i = 0; i < channels[0].length; i++) {
      let value = 0;
      for (const channel of channels) value += channel[i] / channels.length;
      value = Math.max(-1, Math.min(1, value));
      this.buffer[this.used++] = Math.round(value * (value < 0 ? 32768 : 32767));
      if (this.used === this.buffer.length) {
        this.port.postMessage(this.buffer.buffer, [this.buffer.buffer]);
        this.buffer = new Int16Array(9600);
        this.used = 0;
      }
    }
    return true;
  }
}

registerProcessor("pcm-capture", PcmCapture);
