"""字幕格式化单测（纯函数）。"""
import json

from app.schemas import Segment
from app.subtitle import export_segments, to_json, to_srt, to_txt, to_vtt

SEGS = [
    Segment(start_ms=0, end_ms=5890, text="今天天气非常好，好不好？"),
    Segment(start_ms=6170, end_ms=9730, text="这是一个测试。", speaker="spk0"),
]


def test_srt_format():
    out = to_srt(SEGS)
    assert "1\n00:00:00,000 --> 00:00:05,890\n今天天气非常好，好不好？" in out
    assert "2\n00:00:06,170 --> 00:00:09,730\n[spk0] 这是一个测试。" in out


def test_text_with_embedded_newlines_does_not_break_cue():
    # 文本里的换行/空行不能破坏 SRT/VTT 的「块以空行分隔」结构
    segs = [
        Segment(start_ms=0, end_ms=1000, text="hello\n\nworld"),
        Segment(start_ms=1000, end_ms=2000, text="second"),
    ]
    srt = to_srt(segs)
    assert "hello world" in srt          # 内部换行被折叠成空格
    assert "hello\n\nworld" not in srt    # 不留空行
    # 仍是两块两条
    assert srt.count("-->") == 2
    vtt = to_vtt(segs)
    assert "hello world" in vtt
    assert "\n\n\n" not in vtt


def test_vtt_format():
    out = to_vtt(SEGS)
    assert out.startswith("WEBVTT")
    assert "00:00:00.000 --> 00:00:05.890" in out  # VTT 用 '.' 分隔


def test_txt_format():
    out = to_txt(SEGS)
    assert out == "今天天气非常好，好不好？\n[spk0] 这是一个测试。"


def test_json_format():
    data = json.loads(to_json(SEGS))
    assert data[0] == {"start_ms": 0, "end_ms": 5890, "text": "今天天气非常好，好不好？"}
    assert data[1]["speaker"] == "spk0"


def test_export_dispatch_and_bad_format():
    assert export_segments(SEGS, "srt") == to_srt(SEGS)
    try:
        export_segments(SEGS, "xml")
        assert False, "should raise"
    except ValueError:
        pass


def test_timestamp_negative_clamped():
    s = [Segment(start_ms=-100, end_ms=1000, text="x")]
    assert "00:00:00,000 --> 00:00:01,000" in to_srt(s)
