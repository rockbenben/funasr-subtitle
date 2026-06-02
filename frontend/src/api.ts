// Centralized REST + WebSocket access for the funasr-subtitle backend.
//
// All URLs are relative so the app works both behind the Vite dev proxy
// and when served by FastAPI StaticFiles in production (single origin).

import type {
  ExportFormat,
  Job,
  JobEvent,
  Language,
  ModelEvent,
  ModelInfo,
} from "./types";

export interface ApiError {
  code: string;
  message: string;
}

export class SubtitleApiError extends Error {
  code: string;
  constructor(err: ApiError) {
    super(err.message);
    this.name = "SubtitleApiError";
    this.code = err.code;
  }
}

async function parseError(res: Response): Promise<never> {
  let code = `http_${res.status}`;
  let message = res.statusText || "请求失败";
  try {
    const body = await res.json();
    if (body && body.error) {
      code = body.error.code ?? code;
      message = body.error.message ?? message;
    }
  } catch {
    // body was not JSON; keep defaults
  }
  throw new SubtitleApiError({ code, message });
}

async function getJson<T>(url: string): Promise<T> {
  const res = await fetch(url);
  if (!res.ok) return parseError(res);
  return res.json() as Promise<T>;
}

async function postJson<T>(url: string): Promise<T> {
  const res = await fetch(url, { method: "POST" });
  if (!res.ok) return parseError(res);
  return res.json() as Promise<T>;
}

// ---- REST ----

export interface HealthResponse {
  status: string;
  version: string;
}

export function getHealth(): Promise<HealthResponse> {
  return getJson<HealthResponse>("/api/health");
}

export interface ModelsResponse {
  models: ModelInfo[];
  default_id: string;
  compute?: string; // "GPU · CUDA" | "CPU"
}

export function getModels(): Promise<ModelsResponse> {
  return getJson<ModelsResponse>("/api/models");
}

export function downloadModel(id: string): Promise<{ ok: boolean }> {
  return postJson<{ ok: boolean }>(`/api/models/${encodeURIComponent(id)}/download`);
}

export interface CreateJobOptions {
  modelId: string;
  language: Language;
  diarization: boolean;
  hotwords: string;
  useGpu: boolean;
  maxChars: number;
  punctuation: "auto" | "on" | "off";
}

export async function createJob(
  file: File,
  opts: CreateJobOptions,
): Promise<{ job_id: string }> {
  const form = new FormData();
  form.append("file", file);
  if (opts.modelId) form.append("model_id", opts.modelId);
  form.append("language", opts.language);
  form.append("diarization", opts.diarization ? "true" : "false");
  form.append("hotwords", opts.hotwords);
  form.append("use_gpu", opts.useGpu ? "true" : "false");
  form.append("max_chars", String(opts.maxChars));
  form.append("punctuation", opts.punctuation);

  const res = await fetch("/api/jobs", { method: "POST", body: form });
  if (!res.ok) return parseError(res);
  return res.json() as Promise<{ job_id: string }>;
}

export function getJob(jobId: string): Promise<Job> {
  return getJson<Job>(`/api/jobs/${encodeURIComponent(jobId)}`);
}

export function cancelJob(jobId: string): Promise<{ ok: boolean }> {
  return postJson<{ ok: boolean }>(`/api/jobs/${encodeURIComponent(jobId)}/cancel`);
}

export function exportUrl(jobId: string, format: ExportFormat): string {
  return `/api/jobs/${encodeURIComponent(jobId)}/export?format=${format}`;
}

export function shutdown(): Promise<{ ok: boolean }> {
  return postJson<{ ok: boolean }>("/api/shutdown");
}

// ---- WebSocket helpers ----

function wsUrl(path: string): string {
  const scheme = location.protocol === "https:" ? "wss:" : "ws:";
  return `${scheme}//${location.host}${path}`;
}

export interface WsHandle {
  close: () => void;
}

interface WsCallbacks<E> {
  onEvent: (ev: E) => void;
  onError?: (err: Error) => void;
  onClose?: () => void;
}

function openTypedSocket<E>(path: string, cb: WsCallbacks<E>): WsHandle {
  const ws = new WebSocket(wsUrl(path));
  let closedByUs = false;

  ws.onmessage = (msg) => {
    try {
      const data = JSON.parse(msg.data) as E;
      cb.onEvent(data);
    } catch {
      cb.onError?.(new Error("收到无法解析的 WebSocket 消息"));
    }
  };
  ws.onerror = () => {
    if (!closedByUs) cb.onError?.(new Error("WebSocket 连接错误"));
  };
  ws.onclose = () => {
    if (!closedByUs) cb.onClose?.();
  };

  return {
    close: () => {
      closedByUs = true;
      if (
        ws.readyState === WebSocket.OPEN ||
        ws.readyState === WebSocket.CONNECTING
      ) {
        ws.close();
      }
    },
  };
}

export function subscribeJob(
  jobId: string,
  cb: WsCallbacks<JobEvent>,
): WsHandle {
  return openTypedSocket<JobEvent>(
    `/ws/jobs/${encodeURIComponent(jobId)}`,
    cb,
  );
}

export function subscribeModelDownload(
  id: string,
  cb: WsCallbacks<ModelEvent>,
): WsHandle {
  return openTypedSocket<ModelEvent>(
    `/ws/models/${encodeURIComponent(id)}`,
    cb,
  );
}
