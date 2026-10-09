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

export interface Segment {
  start_ms: number;
  end_ms: number;
  text: string;
  speaker?: string;
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
    punctuation?: "auto" | "on" | "off";
  };
  segments: Segment[];
  error?: { code: string; message: string };
  created_at: string;
}

export interface ModelInfo {
  id: string;
  name: string;
  task: string;
  size_mb: number;
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
  | { type: "partial"; segment: Segment }
  | { type: "done"; job: Job }
  | { type: "error"; code: string; message: string; detail?: string };

export type ModelEvent =
  | { type: "download"; percent: number; downloaded_mb: number; total_mb: number }
  | { type: "done" }
  | { type: "error"; code: string; message: string };

export type ExportFormat = "srt" | "vtt" | "txt" | "json";
