import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  App as AntApp,
  Alert,
  Button,
  Card,
  Flex,
  Input,
  InputNumber,
  Popconfirm,
  Progress,
  Segmented,
  Select,
  Space,
  Steps,
  Switch,
  Tag,
  Tooltip,
  Typography,
  Upload,
} from "antd";
import {
  AudioOutlined,
  DownloadOutlined,
  PoweroffOutlined,
  ReloadOutlined,
} from "@ant-design/icons";
import {
  cancelJob,
  createJob,
  downloadModel,
  exportUrl,
  getModels,
  shutdown,
  subscribeJob,
  subscribeModelDownload,
  SubtitleApiError,
  type WsHandle,
} from "./api";
import { LANGUAGE_OPTIONS, STAGE_LABELS } from "./labels";
import type {
  ExportFormat,
  Job,
  Language,
  ModelInfo,
  Segment,
  Stage,
} from "./types";
import { STUDIO } from "./theme";

const { Text } = Typography;
const EXPORT_FORMATS: ExportFormat[] = ["srt", "vtt", "txt", "json"];
const TRANSLATOR_URL = "https://tools.newzone.top/zh/subtitle-translator";
// 接受范围尽量大：MIME 通配 + 一长串显式扩展名（很多容器浏览器不会自动归类成 audio/video，
// 如 .ts/.mka/.opus/.amr）。后端用 ffmpeg/ffprobe 探测，只要有音轨就能转。
const ACCEPT_MEDIA = [
  "audio/*", "video/*",
  // 音频
  ".mp3", ".m4a", ".aac", ".wav", ".flac", ".ogg", ".oga", ".opus", ".wma",
  ".aiff", ".aif", ".alac", ".ape", ".wv", ".amr", ".ac3", ".mka", ".caf", ".dts",
  // 视频
  ".mp4", ".m4v", ".mkv", ".mov", ".avi", ".wmv", ".flv", ".webm", ".ts", ".m2ts",
  ".mts", ".vob", ".mpg", ".mpeg", ".3gp", ".3g2", ".ogv", ".rmvb", ".asf", ".f4v",
].join(",");
// 进度阶段顺序（diar 本期不支持，跳过）
const STAGE_FLOW: Stage[] = ["decoding", "vad", "asr", "punc", "assembling"];

function formatTimestamp(ms: number): string {
  const totalMs = Math.max(0, Math.round(ms));
  const h = Math.floor(totalMs / 3_600_000);
  const m = Math.floor((totalMs % 3_600_000) / 60_000);
  const s = Math.floor((totalMs % 60_000) / 1000);
  const ms3 = totalMs % 1000;
  const pad = (n: number, w = 2) => String(n).padStart(w, "0");
  return `${pad(h)}:${pad(m)}:${pad(s)}.${pad(ms3, 3)}`;
}

// 卡标题统一「大写拉丁 · 中文」：拉丁靠字距撑开，中文段单独收字距，否则被拆成散字。
function SectionLabel({ en, children }: { en: string; children?: React.ReactNode }) {
  return (
    <div className="label-cap" style={{ marginBottom: 10 }}>
      {en}
      {children ? <> · <span className="cap-cn">{children}</span></> : null}
    </div>
  );
}

function FieldLabel({ children, hint }: { children: React.ReactNode; hint?: React.ReactNode }) {
  return (
    <Flex vertical gap={2}>
      <Text type="secondary" style={{ fontSize: 12 }}>{children}</Text>
      {hint ? <Text type="secondary" style={{ fontSize: 11.5, opacity: 0.85 }}>{hint}</Text> : null}
    </Flex>
  );
}

// ----- 模型下载面板（首启，以及「选了还没下载的模型」时复用）-----
function ModelDownloadPanel({
  model,
  caption = "先下模型",
  onDownloaded,
}: {
  model: ModelInfo;
  caption?: string;
  onDownloaded: () => void;
}) {
  const { message } = AntApp.useApp();
  const [downloading, setDownloading] = useState(false);
  const [percent, setPercent] = useState(0);
  const [detail, setDetail] = useState("");
  const wsRef = useRef<WsHandle | null>(null);
  useEffect(() => () => wsRef.current?.close(), []);

  const start = useCallback(async () => {
    setPercent(0);
    setDetail("");
    setDownloading(true);
    wsRef.current?.close();
    wsRef.current = subscribeModelDownload(model.id, {
      onEvent: (ev) => {
        if (ev.type === "download") {
          setPercent(ev.percent);
          setDetail(`${ev.downloaded_mb.toFixed(0)} / ${ev.total_mb.toFixed(0)} MB`);
        } else if (ev.type === "done") {
          setPercent(100);
          setDownloading(false);
          wsRef.current?.close();
          wsRef.current = null;
          message.success("模型已就绪");
          onDownloaded();
        } else if (ev.type === "error") {
          message.error("下载失败，点重试");
          setDownloading(false);
        }
      },
      onError: () => {
        message.error("连不上后台了，下载中断");
        setDownloading(false);
      },
    });
    try {
      await downloadModel(model.id);
    } catch (e) {
      message.error(e instanceof SubtitleApiError ? e.message : "下载没发出去，重试一下");
      setDownloading(false);
      wsRef.current?.close();
      wsRef.current = null;
    }
  }, [model.id, onDownloaded, message]);

  return (
    <Card className="rise d1" variant="outlined" styles={{ body: { padding: 22 } }}>
      <SectionLabel en="FIRST RUN">{caption}</SectionLabel>
      <Flex justify="space-between" align="center" gap={16} wrap>
        <Text type="secondary">
          第一次要用，需要先下模型 <Text strong style={{ color: STUDIO.text }}>{model.name}</Text>
          （约 {model.size_mb.toFixed(0)} MB）。下完就彻底离线，不再联网。
        </Text>
        <Button color="primary" variant="solid" loading={downloading} onClick={start}>
          {downloading ? "正在下载…" : "下载模型"}
        </Button>
      </Flex>
      {(downloading || percent > 0) && (
        <div style={{ marginTop: 14 }}>
          <Progress percent={Math.round(percent)} strokeColor={STUDIO.amber} />
          {detail && <Text type="secondary" className="mono" style={{ fontSize: 12 }}>{detail}</Text>}
        </div>
      )}
    </Card>
  );
}

// ----- 结果面板 -----
function ResultsPanel({ job }: { job: Job }) {
  const { message } = AntApp.useApp();
  const [copied, setCopied] = useState(false);
  const fullText = useMemo(() => job.segments.map((s) => s.text).join("\n"), [job.segments]);
  const copyAll = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(fullText);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2000);
      message.success(`已复制 ${job.segments.length} 句`);
    } catch {
      message.error("复制没成功，再试一次");
    }
  }, [fullText, job.segments.length, message]);

  return (
    <Card className="rise" variant="outlined">
      <Flex justify="space-between" align="center" wrap gap={12} style={{ marginBottom: 14 }}>
        <SectionLabel en="TRANSCRIPT">字幕 · {job.segments.length} 句</SectionLabel>
        <Space wrap>
          {EXPORT_FORMATS.map((fmt) => (
            <Button
              key={fmt}
              size="small"
              variant="outlined"
              icon={<DownloadOutlined />}
              href={exportUrl(job.id, fmt)}
              download
              onClick={() => message.info(`已开始下载 .${fmt}`)}
            >
              .{fmt}
            </Button>
          ))}
          <Button size="small" color="primary" variant="filled" onClick={copyAll}>
            {copied ? "已复制 ✓" : "复制全文"}
          </Button>
        </Space>
      </Flex>
      <Text type="secondary" style={{ fontSize: 12 }}>
        想翻成双语字幕？把 .srt 导入{" "}
        <a href={TRANSLATOR_URL} target="_blank" rel="noreferrer">字幕翻译器</a>
      </Text>
      <div
        className="scroll-fade"
        style={{ marginTop: 12, maxHeight: 420, overflow: "auto", paddingRight: 6 }}
      >
        {job.segments.length === 0 ? (
          <Text type="secondary">
            没听到人声。可能是纯音乐、静音，或者这个文件没有音轨。换个文件再试。
          </Text>
        ) : (
          job.segments.map((seg: Segment, i) => (
            <div className="seg-row" key={i}>
              <span className="ts" style={{ fontSize: 12.5 }}>
                {formatTimestamp(seg.start_ms)}
                <br />
                {formatTimestamp(seg.end_ms)}
              </span>
              <span className="seg-text">
                {seg.speaker && <Tag color="gold" style={{ marginRight: 6 }}>{seg.speaker}</Tag>}
                {seg.text}
              </span>
            </div>
          ))
        )}
      </div>
    </Card>
  );
}

// ----- 主应用 -----
export default function App() {
  const { message } = AntApp.useApp();
  const [models, setModels] = useState<ModelInfo[]>([]);
  const [defaultId, setDefaultId] = useState<string>("");
  const [modelsError, setModelsError] = useState<string | null>(null);

  const [file, setFile] = useState<File | null>(null);
  const [modelId, setModelId] = useState<string>("");
  const [language, setLanguage] = useState<Language>("auto");
  const [diarization, setDiarization] = useState(false);
  const [hotwords, setHotwords] = useState("");
  const [maxChars, setMaxChars] = useState(30);
  const [punctuation, setPunctuation] = useState<"auto" | "on" | "off">("auto");
  const [compute, setCompute] = useState("CPU");
  // DirectML 实测对量化模型比 CPU 慢，已隐藏开关；后端仍保留 use_gpu 能力（默认 false）。
  const useGpu = false;

  const [job, setJob] = useState<Job | null>(null);
  const [stage, setStage] = useState<Stage | null>(null);
  const [percent, setPercent] = useState(0);
  const [running, setRunning] = useState(false);
  const [jobError, setJobError] = useState<string | null>(null);
  const [jobDetail, setJobDetail] = useState<string | null>(null);
  const [cancelled, setCancelled] = useState(false);
  // 任务开始时选的模型还没下载：后端会先在 job 线程里下模型，界面必须说清进度为什么不动。
  const [fetchingModel, setFetchingModel] = useState(false);
  const jobWsRef = useRef<WsHandle | null>(null);

  const loadModels = useCallback(async () => {
    setModelsError(null);
    try {
      const res = await getModels();
      setModels(res.models);
      setDefaultId(res.default_id);
      setModelId((cur) => cur || res.default_id);
      if (res.compute) setCompute(res.compute);
    } catch (e) {
      setModelsError(e instanceof SubtitleApiError ? e.message : "拿不到模型列表，后台可能还没起来");
    }
  }, []);

  useEffect(() => void loadModels(), [loadModels]);
  useEffect(() => () => jobWsRef.current?.close(), []);

  const defaultModel = useMemo(() => models.find((m) => m.id === defaultId), [models, defaultId]);
  const needsDownload = !!defaultModel && !defaultModel.downloaded;
  const currentModel = useMemo(() => models.find((m) => m.id === modelId), [models, modelId]);
  const supportsHotwords = /contextual/.test(modelId);
  const diarAvailable = !!currentModel?.diarization;  // 仅完整版 + Paraformer
  const modelNotDownloaded = !!currentModel && !currentModel.downloaded;

  const clearRunState = useCallback(() => {
    setJob(null);
    setJobError(null);
    setJobDetail(null);
    setStage(null);
    setPercent(0);
    setCancelled(false);
    setFetchingModel(false);
  }, []);

  const pickFile = (f: File | null) => {
    setFile(f);
    clearRunState();
  };

  const start = useCallback(async () => {
    if (!file || running) return;
    setRunning(true);
    clearRunState();
    setFetchingModel(modelNotDownloaded);
    try {
      const { job_id } = await createJob(file, {
        modelId,
        language,
        diarization: diarization && diarAvailable,  // 不可用时不发送
        hotwords: hotwords.trim(),
        useGpu,
        maxChars,
        punctuation,
      });
      jobWsRef.current?.close();
      let settled = false;  // 是否已收到终态，用于区分「正常关闭」与「意外断开」
      jobWsRef.current = subscribeJob(job_id, {
        onEvent: (ev) => {
          if (ev.type === "progress") {
            setStage(ev.stage);
            setPercent(ev.percent);
          } else if (ev.type === "done") {
            settled = true;
            setJob(ev.job);
            setPercent(100);
            setRunning(false);
            setFetchingModel(false);
            jobWsRef.current?.close();
            jobWsRef.current = null;
          } else if (ev.type === "error") {
            settled = true;
            setRunning(false);
            setFetchingModel(false);
            // 用户主动取消不是失败：后端用 code=cancelled 报，这里按取消收口。
            if (ev.code === "cancelled") {
              setJob(null);
              setStage(null);
              setPercent(0);
              setCancelled(true);
            } else {
              setJobError(ev.message || "转写没完成");
              setJobDetail(ev.detail ?? null);
            }
          }
        },
        onError: () => {
          setJobError((prev) => prev ?? "连不上后台了，转写中断");
          setRunning(false);
        },
        onClose: () => {
          // 没收到终态就断开（服务重启/代理掉线）：别让界面永远卡在「正在转写」
          if (!settled) {
            setJobError((prev) => prev ?? "后台断了，请点重试");
            setRunning(false);
          }
        },
      });
      setJob({
        id: job_id,
        filename: file.name,
        status: "queued",
        percent: 0,
        options: { model_id: modelId, language, diarization: diarization && diarAvailable, hotwords, use_gpu: useGpu, max_chars: maxChars, punctuation },
        segments: [],
        created_at: new Date().toISOString(),
      });
    } catch (e) {
      if (e instanceof SubtitleApiError) {
        setJobError(e.message);
        setJobDetail(e.detail ?? null);
      } else {
        setJobError("任务没发出去，重试一下");
      }
      setRunning(false);
      setFetchingModel(false);
    }
  }, [file, running, modelId, language, diarization, diarAvailable, hotwords, maxChars, punctuation, modelNotDownloaded, clearRunState]);

  // 取消：不管后端什么时候确认，界面立刻从「在跑」回到「可重来」，不留半截进度卡。
  const onCancel = useCallback(async () => {
    if (!job) return;
    jobWsRef.current?.close();
    jobWsRef.current = null;
    setRunning(false);
    clearRunState();
    setCancelled(true);  // 屏上的「已取消转写」回执就是反馈，不再叠一条 toast
    try {
      await cancelJob(job.id);
    } catch { /* best-effort */ }
  }, [job, clearRunState]);

  const onShutdown = useCallback(async () => {
    try {
      await shutdown();
      message.info("服务已停止，可以关掉这个页面了");
    } catch { /* process may close before responding */ }
  }, [message]);

  const isDone = !!job && job.status === "done";
  const showProgress = running || (!!job && !isDone && !jobError && !cancelled);
  const stageIndex = stage ? STAGE_FLOW.indexOf(stage) : -1;
  const startLabel = running
    ? "正在转写…"
    : isDone
      ? "重新转写"
      : modelNotDownloaded
        ? "下载并转写"
        : "开始转写";

  // 结果卡排在设置之前：跑完第一眼就能看到字幕（否则整块落在折叠下方）。
  const results = isDone && job ? <ResultsPanel job={job} /> : null;

  return (
    <div style={{ position: "relative", zIndex: 1, minHeight: "100%", padding: "32px 20px 80px" }}>
      <div style={{ maxWidth: 860, margin: "0 auto" }}>
        {/* 走带条 / Transport bar */}
        <Flex justify="space-between" align="center" className="rise" style={{ marginBottom: 26 }}>
          <Flex align="center" gap={12} wrap>
            <span className={`rec-dot${running ? " live" : ""}`} />
            <span className="wordmark nowrap" style={{ fontSize: 20 }}>funasr-subtitle</span>
            <span className="label-cap nowrap" style={{ marginLeft: 6 }}>
              <span className="cap-cn">本地 · 离线 · 转写</span>
            </span>
          </Flex>
          <Space size={10}>
            <Tag color={compute.startsWith("GPU") ? "gold" : "default"} style={{ marginRight: 0 }}>
              {compute.startsWith("GPU") ? "用显卡跑" : "用 CPU 跑"}
            </Tag>
            <Popconfirm
              title="要停止后台服务吗？"
              description="正在跑的任务会中断，页面可以关掉。"
              okText="停止服务"
              cancelText="取消"
              okButtonProps={{ danger: true, variant: "outlined" }}
              onConfirm={onShutdown}
            >
              <Button className="btn-exit" color="danger" variant="text" icon={<PoweroffOutlined />} size="small">
                停止服务
              </Button>
            </Popconfirm>
          </Space>
        </Flex>

        <Flex vertical gap={18}>
          {modelsError && (
            <Alert
              type="error"
              showIcon
              message={modelsError}
              action={<Button size="small" icon={<ReloadOutlined />} onClick={() => void loadModels()}>重新获取模型</Button>}
            />
          )}

          {needsDownload && defaultModel && (
            <ModelDownloadPanel model={defaultModel} onDownloaded={loadModels} />
          )}

          {/* 装载 / 拖放区 */}
          <Card className="rise d1" variant="outlined">
            <SectionLabel en="SOURCE">音视频</SectionLabel>
            <Upload.Dragger
              className="drop-zone"
              accept={ACCEPT_MEDIA}
              multiple={false}
              showUploadList={false}
              beforeUpload={(f) => {
                pickFile(f as unknown as File);
                return false;
              }}
              style={{ background: "transparent" }}
            >
              <Flex vertical align="center" gap={10} style={{ padding: "14px 0" }}>
                <span className="eq"><i /><i /><i /><i /><i /></span>
                {file ? (
                  <Text className="mono" style={{ color: STUDIO.amberSoft, fontSize: 15 }}>{file.name}</Text>
                ) : (
                  <Text type="secondary" className="label-hint">把音频或视频拖进来，也可以点击选择文件</Text>
                )}
                <Text type="secondary" style={{ fontSize: 12 }}>
                  常见格式都能读：mp4、mkv、mov、mp3、wav……只要里面有声音
                </Text>
              </Flex>
            </Upload.Dragger>
          </Card>

          {results}

          {/* 设置 */}
          <Card className="rise d2" variant="outlined">
            <SectionLabel en="SETTINGS">{isDone ? "设置 · 可改参数后重跑" : "设置"}</SectionLabel>
            <Flex vertical gap={18}>
              <Flex gap={28} wrap align="flex-start">
                <Flex vertical gap={8} style={{ minWidth: 0, maxWidth: "100%" }}>
                  <Text type="secondary" style={{ fontSize: 12 }}>语言</Text>
                  <div className="seg-scroll">
                    <Segmented
                      value={language}
                      onChange={(v) => setLanguage(v as Language)}
                      options={LANGUAGE_OPTIONS.map((o) => ({ label: o.label, value: o.value }))}
                    />
                  </div>
                </Flex>
                <Flex vertical gap={8} style={{ minWidth: 280, flex: 1 }}>
                  <Text type="secondary" style={{ fontSize: 12 }}>模型</Text>
                  <Select
                    value={modelId || undefined}
                    onChange={setModelId}
                    loading={models.length === 0}
                    options={models.map((m) => ({
                      value: m.id,
                      label: m.downloaded ? m.name : `${m.name}（未下载）`,
                    }))}
                  />
                </Flex>
              </Flex>

              <Flex gap={28} wrap align="flex-end">
                <Flex vertical gap={8} style={{ flex: 1, minWidth: 240 }}>
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    热词{!supportsHotwords && "（只有「热词版」模型认）"}
                  </Text>
                  <Input
                    value={hotwords}
                    onChange={(e) => setHotwords(e.target.value)}
                    placeholder="输入关键词，用空格分开（可不填）"
                    disabled={!supportsHotwords}
                  />
                </Flex>
                <Flex vertical gap={8} style={{ flex: 1, minWidth: 180, maxWidth: 260 }}>
                  <Tooltip title="一行显示多少个字符。中文按字算，英文两个字母算一个。填 0 就只按句子断，不控长度。">
                    <FieldLabel hint="0 = 不按长度断句">每行最多几个字</FieldLabel>
                  </Tooltip>
                  <InputNumber
                    min={0}
                    max={100}
                    value={maxChars}
                    onChange={(v) => setMaxChars((prev) => (v == null ? prev : v))}
                    style={{ width: "100%" }}
                  />
                </Flex>
                <Flex vertical gap={8}>
                  <Tooltip title="自动：中文加标点，英文按停顿断句（英文标点容易加错）。加标点：不管什么语言都加。不加标点：只按停顿和行宽断。">
                    <FieldLabel>标点</FieldLabel>
                  </Tooltip>
                  <Segmented
                    value={punctuation}
                    onChange={(v) => setPunctuation(v as "auto" | "on" | "off")}
                    options={[
                      { label: "自动", value: "auto" },
                      { label: "加标点", value: "on" },
                      { label: "不加标点", value: "off" },
                    ]}
                  />
                </Flex>
              </Flex>

              <Tooltip title={diarAvailable
                ? "给每句字幕标上是谁说的，导出的字幕里会带 [spk0]、[spk1] 这样的记号"
                : "只有「完整版」程序配 Paraformer 模型能区分说话人。轻量版（onnx）和 SenseVoice 做不到。"}>
                <Flex align="center" gap={10}>
                  <span className="switch-hit">
                    <Switch checked={diarization && diarAvailable} disabled={!diarAvailable} onChange={setDiarization} />
                  </span>
                  <Text type={diarAvailable ? undefined : "secondary"}>
                    区分说话人{diarAvailable ? "" : "（需完整版 + Paraformer）"}
                  </Text>
                </Flex>
              </Tooltip>

              {!needsDownload && modelNotDownloaded && currentModel && (
                <ModelDownloadPanel
                  model={currentModel}
                  caption="这个模型还没下载"
                  onDownloaded={loadModels}
                />
              )}

              <Flex vertical gap={8}>
                <Flex gap={12} align="center" wrap>
                  <Button
                    color="primary"
                    variant="solid"
                    size="large"
                    icon={<AudioOutlined />}
                    disabled={!file}
                    loading={running}
                    onClick={start}
                  >
                    {startLabel}
                  </Button>
                  {running && <Button variant="outlined" onClick={onCancel}>取消</Button>}
                  {!running && file && (
                    <Button variant="text" onClick={() => pickFile(null)}>换个文件</Button>
                  )}
                </Flex>
                {!file && (
                  <Text type="secondary" style={{ fontSize: 12 }}>先选一个音视频文件，才能开始转写。</Text>
                )}
              </Flex>
            </Flex>
          </Card>

          {/* 进度 */}
          {showProgress && (
            <Card className="rise" variant="outlined">
              <SectionLabel en="PROGRESS">{stage ? STAGE_LABELS[stage] : "等待开始"}</SectionLabel>
              <Steps
                size="small"
                current={stageIndex}
                items={STAGE_FLOW.map((s) => ({ title: STAGE_LABELS[s] }))}
                style={{ marginBottom: 16 }}
              />
              <Progress
                percent={Math.round(percent)}
                strokeColor={{ from: STUDIO.amber, to: STUDIO.amberSoft }}
                status={running ? "active" : "normal"}
              />
              {fetchingModel && (
                <Text type="secondary" style={{ fontSize: 12 }}>
                  第一次用这个模型，正在先下载（约 {currentModel?.size_mb.toFixed(0) ?? "若干"} MB），
                  进度可能长时间停在「读音频」。
                </Text>
              )}
            </Card>
          )}

          {cancelled && !jobError && (
            <Alert
              type="info"
              showIcon
              message="已取消转写"
              description="文件还在，随时可以重新开始。"
              className="rise"
            />
          )}

          {jobError && (
            <Alert
              type="error"
              showIcon
              message="转写没完成"
              description={
                <Flex vertical gap={6}>
                  <span>{jobError}</span>
                  {jobDetail && (
                    <details>
                      <summary style={{ cursor: "pointer" }}>查看详情</summary>
                      <pre className="mono" style={{ whiteSpace: "pre-wrap", fontSize: 11.5, margin: "6px 0 0" }}>
                        {jobDetail}
                      </pre>
                    </details>
                  )}
                </Flex>
              }
              action={<Button size="small" icon={<ReloadOutlined />} onClick={start}>重新转写</Button>}
              className="rise"
            />
          )}
        </Flex>
      </div>
    </div>
  );
}
