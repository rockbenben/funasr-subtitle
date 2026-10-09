"""SRT / VTT / TXT / JSON 格式化。纯函数，无外部依赖，便于单测。"""
from __future__ import annotations

import json
import re
from collections.abc import Iterable

from ..schemas import Segment

# 折叠文本内部的换行/回车（连同周围空白）为单个空格：cue 文本里出现空行会破坏
# SRT/VTT「块以空行分隔」的结构，导致后续条目错位。
_NEWLINE_RUN = re.compile(r"\s*[\r\n]+\s*")

# format -> (mime, 文件扩展名)
EXPORT_FORMATS: dict[str, tuple[str, str]] = {
    "srt": ("application/x-subrip", "srt"),
    "vtt": ("text/vtt", "vtt"),
    "txt": ("text/plain", "txt"),
    "json": ("application/json", "json"),
}


def _clamp(ms: int) -> int:
    return ms if ms > 0 else 0


def _fmt_ts(ms: int, sep: str) -> str:
    """毫秒 -> HH:MM:SS<sep>mmm。SRT 用 ',' VTT 用 '.'。"""
    ms = _clamp(ms)  # 调用方一律传 Segment.start_ms/end_ms（pydantic 已保证是 int）
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1_000)
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{ms:03d}"


def _line_text(seg: Segment) -> str:
    text = _NEWLINE_RUN.sub(" ", seg.text).strip()
    if seg.speaker:
        return f"[{seg.speaker}] {text}"
    return text


def to_srt(segments: Iterable[Segment]) -> str:
    blocks: list[str] = []
    for i, seg in enumerate(segments, start=1):
        start = _fmt_ts(seg.start_ms, ",")
        end = _fmt_ts(seg.end_ms, ",")
        blocks.append(f"{i}\n{start} --> {end}\n{_line_text(seg)}\n")
    return "\n".join(blocks)


def to_vtt(segments: Iterable[Segment]) -> str:
    out = ["WEBVTT", ""]
    for seg in segments:
        start = _fmt_ts(seg.start_ms, ".")
        end = _fmt_ts(seg.end_ms, ".")
        out.append(f"{start} --> {end}")
        out.append(_line_text(seg))
        out.append("")
    return "\n".join(out)


def to_txt(segments: Iterable[Segment]) -> str:
    return "\n".join(_line_text(seg) for seg in segments)


def to_json(segments: Iterable[Segment]) -> str:
    data = [
        {
            "start_ms": seg.start_ms,
            "end_ms": seg.end_ms,
            "text": seg.text.strip(),
            **({"speaker": seg.speaker} if seg.speaker else {}),
        }
        for seg in segments
    ]
    return json.dumps(data, ensure_ascii=False, indent=2)


def export_segments(segments: Iterable[Segment], fmt: str) -> str:
    fmt = fmt.lower()
    if fmt not in EXPORT_FORMATS:
        raise ValueError(f"unsupported export format: {fmt}")
    segs = list(segments)
    return {
        "srt": to_srt,
        "vtt": to_vtt,
        "txt": to_txt,
        "json": to_json,
    }[fmt](segs)
