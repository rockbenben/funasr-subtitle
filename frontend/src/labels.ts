// Chinese UI labels and option maps.

import type { Language, Stage } from "./types";

export const LANGUAGE_OPTIONS: { value: Language; label: string }[] = [
  { value: "auto", label: "自动" },
  { value: "zh", label: "中文" },
  { value: "yue", label: "粤语" },
  { value: "en", label: "英语" },
  { value: "ja", label: "日语" },
  { value: "ko", label: "韩语" },
];

// 阶段名给人看，不写流水线内部词（解码/组装）。
export const STAGE_LABELS: Record<Stage, string> = {
  decoding: "读音频",
  vad: "找语音",
  asr: "转文字",
  punc: "加标点",
  diar: "分说话人",
  assembling: "写字幕",
};
