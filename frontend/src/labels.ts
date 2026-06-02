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

export const STAGE_LABELS: Record<Stage, string> = {
  decoding: "解码",
  vad: "语音切分",
  asr: "识别",
  punc: "标点",
  diar: "说话人",
  assembling: "组装",
};
