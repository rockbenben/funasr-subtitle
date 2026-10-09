"""字幕导出层：segments -> SRT / VTT / TXT / JSON（§6C, §7 export）。

公共数据是 list[Segment]，所有未来模式复用本层（§16）。
"""
from .formatters import (
    EXPORT_FORMATS,
    export_segments,
    to_json,
    to_srt,
    to_txt,
    to_vtt,
)
from .segmentation import (
    enforce_monotonic,
    segment_span,
    segment_timed,
    split_sentences,
    strip_terminal_punct,
)

__all__ = [
    "EXPORT_FORMATS",
    "enforce_monotonic",
    "export_segments",
    "segment_span",
    "segment_timed",
    "split_sentences",
    "strip_terminal_punct",
    "to_json",
    "to_srt",
    "to_txt",
    "to_vtt",
]
