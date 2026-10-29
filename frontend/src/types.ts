// Mirror of the backend data model.

export type JobStatus =
  | "queued"
  | "decoding"
  | "vad"
  | "asr"
  | "punc"
  | "diar"
  | "assembling"
  | "done"
  | "error";

export interface Word {
  text: string;
  start_ms: number;
  end_ms: number;
}

export interface Segment {
  start_ms: number;
  end_ms: number;
  text: string;
  speaker?: string;
  /** 逐字时间戳（卡拉OK / 逐词高亮用）；拿不到逐字时间的路径没有这个字段。 */
  words?: Word[];
}

export interface Job {
  id: string;
  filename: string;
  status: JobStatus;
  percent: number;
  options: {
    model_id: string;
    language: string;
    diarization: boolean;
    hotwords: string;
    use_gpu?: boolean;
    max_chars?: number;
    pause_ms?: number;
    itn?: boolean;
    punctuation?: "auto" | "on" | "off";
  };
  segments: Segment[];
  error?: { code: string; message: string; detail?: string };
  created_at: string;
}

export interface ModelInfo {
  id: string;
  name: string;
  task: string;
  /** 按后端取的下载体积（MB）：onnx 后端几百，完整版 0.9–2.1 GB。 */
  size_mb: number;
  /** 按后端取的「已就位」：onnx 看 onnx 仓库，完整版看 pt 权重。 */
  downloaded: boolean;
  diarization?: boolean; // 该模型在当前后端是否支持说话人分离（仅完整版 + Paraformer）
}

// ---- option enums used by the UI ----

export type Language = "auto" | "zh" | "yue" | "en" | "ja" | "ko";

// Pipeline stage as pushed over WS progress events.
export type Stage =
  | "decoding"
  | "vad"
  | "asr"
  | "punc"
  | "diar"
  | "assembling";

// ---- WebSocket event payloads ----

export type JobEvent =
  | { type: "progress"; stage: Stage; percent: number }
  // "partial" 是给「流式出句」预留的：后端目前**不发**这个事件，留着是为了将来
  // 增量出字幕时不用改类型定义。真收到时按增量句处理即可。
  | { type: "partial"; segment: Segment }
  | { type: "done"; job: Job }
  | { type: "error"; code: string; message: string; detail?: string };

export type ModelEvent =
  | { type: "download"; percent: number; downloaded_mb: number; total_mb: number }
  | { type: "done" }
  // detail 是后端异常的原文（如 modelscope 报错），界面折叠进「查看详情」。
  | { type: "error"; code: string; message: string; detail?: string };

export type ExportFormat = "srt" | "vtt" | "txt" | "json";
