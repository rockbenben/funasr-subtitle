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

function SectionLabel({ children }: { children: React.ReactNode }) {
  return <div className="label-cap" style={{ marginBottom: 10 }}>{children}</div>;
}

// ----- 模型下载面板 -----
function ModelDownloadPanel({
  model,
  onDownloaded,
}: {
  model: ModelInfo;
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
          message.error(ev.message || "下载失败");
          setDownloading(false);
        }
      },
      onError: () => {
        message.error("下载连接中断");
        setDownloading(false);
      },
    });
    try {
      await downloadModel(model.id);
    } catch (e) {
      message.error(e instanceof SubtitleApiError ? e.message : "下载请求失败");
      setDownloading(false);
      wsRef.current?.close();
      wsRef.current = null;
    }
  }, [model.id, onDownloaded, message]);

  return (
    <Card className="rise d1" variant="outlined" styles={{ body: { padding: 22 } }}>
      <SectionLabel>FIRST RUN · 首启下载</SectionLabel>
      <Flex justify="space-between" align="center" gap={16} wrap>
        <Text type="secondary">
          需下载默认模型 <Text strong style={{ color: STUDIO.text }}>{model.name}</Text>
          （约 {model.size_mb.toFixed(0)} MB），之后完全离线可用。
        </Text>
        <Button color="primary" variant="solid" loading={downloading} onClick={start}>
          {downloading ? "下载中…" : "下载模型"}
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
  const fullText = useMemo(() => job.segments.map((s) => s.text).join("\n"), [job.segments]);
  const copyAll = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(fullText);
      message.success("已复制全文");
    } catch {
      message.error("复制失败");
    }
  }, [fullText, message]);

  return (
    <Card className="rise" variant="outlined">
      <Flex justify="space-between" align="center" wrap gap={12} style={{ marginBottom: 14 }}>
        <SectionLabel>TRANSCRIPT · {job.segments.length} 段</SectionLabel>
        <Space wrap>
          {EXPORT_FORMATS.map((fmt) => (
            <Button key={fmt} size="small" variant="outlined" href={exportUrl(job.id, fmt)} download>
              .{fmt}
            </Button>
          ))}
          <Button size="small" color="primary" variant="filled" onClick={copyAll}>
            复制全文
          </Button>
        </Space>
      </Flex>
      <Text type="secondary" style={{ fontSize: 12 }}>
        .srt 可直接导入 subtitle-translator 翻译
      </Text>
      <div style={{ marginTop: 12, maxHeight: 420, overflow: "auto", paddingRight: 6 }}>
        {job.segments.length === 0 ? (
          <Text type="secondary">（无文本片段）</Text>
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
      setModelsError(e instanceof SubtitleApiError ? e.message : "无法获取模型列表");
    }
  }, []);

  useEffect(() => void loadModels(), [loadModels]);
  useEffect(() => () => jobWsRef.current?.close(), []);

  const defaultModel = useMemo(() => models.find((m) => m.id === defaultId), [models, defaultId]);
  const needsDownload = !!defaultModel && !defaultModel.downloaded;
  const currentModel = useMemo(() => models.find((m) => m.id === modelId), [models, modelId]);
  const supportsHotwords = /contextual/.test(modelId);
  const diarAvailable = !!currentModel?.diarization;  // 仅完整版 + Paraformer

  const pickFile = (f: File | null) => {
    setFile(f);
    setJob(null);
    setJobError(null);
    setStage(null);
    setPercent(0);
  };

  const start = useCallback(async () => {
    if (!file || running) return;
    setRunning(true);
    setJob(null);
    setJobError(null);
    setStage(null);
    setPercent(0);
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
      let settled = false;  // 是否已收到终态(done/error)，用于区分「正常关闭」与「意外断开」
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
            jobWsRef.current?.close();
            jobWsRef.current = null;
          } else if (ev.type === "error") {
            settled = true;
            setJobError(ev.message || "转写失败");
            setRunning(false);
          }
        },
        onError: () => {
          setJobError((prev) => prev ?? "转写连接中断");
          setRunning(false);
        },
        onClose: () => {
          // 没收到 done/error 就断开（服务重启/代理掉线）：别让界面永远卡在「转写中」
          if (!settled) {
            setJobError((prev) => prev ?? "连接意外断开，请重试");
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
      setJobError(e instanceof SubtitleApiError ? e.message : "创建任务失败");
      setRunning(false);
    }
  }, [file, running, modelId, language, diarization, diarAvailable, hotwords, maxChars, punctuation]);

  const onCancel = useCallback(async () => {
    if (!job) return;
    try {
      await cancelJob(job.id);
    } catch { /* best-effort */ }
    jobWsRef.current?.close();
    jobWsRef.current = null;
    setRunning(false);
    setStage(null);
  }, [job]);

  const onShutdown = useCallback(async () => {
    try {
      await shutdown();
      message.info("已退出，可关闭此页");
    } catch { /* process may close before responding */ }
  }, [message]);

  const isDone = !!job && job.status === "done";
  const showProgress = running || (!!job && !isDone && !jobError);
  const stageIndex = stage ? STAGE_FLOW.indexOf(stage) : -1;

  return (
    <div style={{ position: "relative", zIndex: 1, minHeight: "100%", padding: "32px 20px 80px" }}>
      <div style={{ maxWidth: 860, margin: "0 auto" }}>
        {/* 走带条 / Transport bar */}
        <Flex justify="space-between" align="center" className="rise" style={{ marginBottom: 26 }}>
          <Flex align="center" gap={12}>
            <span className={`rec-dot${running ? " live" : ""}`} />
            <span className="wordmark" style={{ fontSize: 20 }}>funasr-subtitle</span>
            <span className="label-cap" style={{ marginLeft: 6 }}>本地 · 离线 转写</span>
          </Flex>
          <Space size={10}>
            <Tag color={compute.startsWith("GPU") ? "gold" : "default"} className="mono" style={{ marginRight: 0 }}>{compute}</Tag>
            <Popconfirm title="退出 funasr-subtitle？" okText="退出" cancelText="取消" onConfirm={onShutdown}>
              <Button color="danger" variant="text" icon={<PoweroffOutlined />} size="small">退出</Button>
            </Popconfirm>
          </Space>
        </Flex>

        <Flex vertical gap={18}>
          {modelsError && (
            <Alert
              type="error"
              showIcon
              message={modelsError}
              action={<Button size="small" icon={<ReloadOutlined />} onClick={() => void loadModels()}>重试</Button>}
            />
          )}

          {needsDownload && defaultModel && (
            <ModelDownloadPanel model={defaultModel} onDownloaded={loadModels} />
          )}

          {/* 装载 / 拖放区 */}
          <Card className="rise d1" variant="outlined">
            <SectionLabel>SOURCE · 音视频</SectionLabel>
            <Upload.Dragger
              accept={ACCEPT_MEDIA}
              multiple={false}
              showUploadList={false}
              beforeUpload={(f) => {
                pickFile(f as unknown as File);
                return false;
              }}
              style={{ background: "transparent", borderColor: STUDIO.border }}
            >
              <Flex vertical align="center" gap={10} style={{ padding: "14px 0" }}>
                <span className="eq"><i /><i /><i /><i /><i /></span>
                {file ? (
                  <Text className="mono" style={{ color: STUDIO.amberSoft, fontSize: 15 }}>{file.name}</Text>
                ) : (
                  <Text type="secondary">把音频 / 视频拖到这里，或点击选择</Text>
                )}
                <Text type="secondary" style={{ fontSize: 12 }}>
                  mp4 · mkv · mov · ts · mp3 · m4a · wav · flac · opus … 几乎任意含音轨的音视频
                </Text>
              </Flex>
            </Upload.Dragger>
          </Card>

          {/* 设置 */}
          <Card className="rise d2" variant="outlined">
            <SectionLabel>SETTINGS · 设置</SectionLabel>
            <Flex vertical gap={18}>
              <Flex gap={28} wrap align="flex-start">
                <Flex vertical gap={8}>
                  <Text type="secondary" style={{ fontSize: 12 }}>语言</Text>
                  <Segmented
                    value={language}
                    onChange={(v) => setLanguage(v as Language)}
                    options={LANGUAGE_OPTIONS.map((o) => ({ label: o.label, value: o.value }))}
                  />
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
                    热词{!supportsHotwords && "（仅热词版模型生效）"}
                  </Text>
                  <Input
                    value={hotwords}
                    onChange={(e) => setHotwords(e.target.value)}
                    placeholder="空格分隔，可空"
                    disabled={!supportsHotwords}
                  />
                </Flex>
                <Flex vertical gap={8}>
                  <Tooltip title="每行最大显示宽度：中文按字数，英文按半字（默认 30≈中文30字/英文60字）。0=不限制，只按句末标点切；超长在标点/词边界断，不拆英文词。">
                    <Text type="secondary" style={{ fontSize: 12 }}>每行最大字数（中文，英文自动放宽；0=不限制）</Text>
                  </Tooltip>
                  <InputNumber
                    min={0}
                    max={100}
                    value={maxChars}
                    onChange={(v) => setMaxChars((prev) => (v == null ? prev : v))}
                    style={{ width: 160 }}
                  />
                </Flex>
                <Flex vertical gap={8}>
                  <Tooltip title="自动：中文等自带标点的语言加句末标点，英文按停顿切、不强加（英文标点常不准）。加句末标点：都加。不加：都只按停顿/行宽切。">
                    <Text type="secondary" style={{ fontSize: 12 }}>标点</Text>
                  </Tooltip>
                  <Segmented
                    value={punctuation}
                    onChange={(v) => setPunctuation(v as "auto" | "on" | "off")}
                    options={[
                      { label: "自动", value: "auto" },
                      { label: "加句末标点", value: "on" },
                      { label: "不加", value: "off" },
                    ]}
                  />
                </Flex>
              </Flex>

              <Tooltip title={diarAvailable
                ? "用 cam++ 标注每段说话人，导出含 [spk0]/[spk1]…"
                : "说话人分离仅在「完整版 + Paraformer 模型」下可用（完整版有 N 卡自动 GPU、无卡回退 CPU）；onnx 版与 SenseVoice 不支持"}>
                <Flex align="center" gap={10}>
                  <Switch checked={diarization && diarAvailable} disabled={!diarAvailable} onChange={setDiarization} />
                  <Text type={diarAvailable ? undefined : "secondary"}>
                    说话人分离{diarAvailable ? "" : "（需完整版+Paraformer）"}
                  </Text>
                </Flex>
              </Tooltip>

              <Flex gap={12} align="center">
                <Button
                  color="primary"
                  variant="solid"
                  size="large"
                  icon={<AudioOutlined />}
                  disabled={!file}
                  loading={running}
                  onClick={start}
                >
                  {running ? "转写中…" : "开始转写"}
                </Button>
                {running && <Button variant="outlined" onClick={onCancel}>取消</Button>}
                {currentModel && !running && (
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    {currentModel.name}
                  </Text>
                )}
              </Flex>
            </Flex>
          </Card>

          {/* 进度 */}
          {showProgress && (
            <Card className="rise" variant="outlined">
              <SectionLabel>PROGRESS · {stage ? STAGE_LABELS[stage] : "排队中"} · {Math.round(percent)}%</SectionLabel>
              <Steps
                size="small"
                current={stageIndex < 0 ? 0 : stageIndex}
                items={STAGE_FLOW.map((s) => ({ title: STAGE_LABELS[s] }))}
                style={{ marginBottom: 16 }}
              />
              <Progress
                percent={Math.round(percent)}
                strokeColor={{ from: STUDIO.amber, to: STUDIO.amberSoft }}
                status={running ? "active" : "normal"}
              />
            </Card>
          )}

          {jobError && <Alert type="error" showIcon message="转写失败" description={jobError} className="rise" />}

          {isDone && job && <ResultsPanel job={job} />}
        </Flex>
      </div>
    </div>
  );
}
