"""完整版（torch 后端）**切段逻辑**的单测——不需要 torch。

完整版的推理要 torch，但「上游 sentence_info → 我们的字幕条」这段是纯逻辑，
用 `FunasrFullEngine.__new__` 绕开 `__init__`（写法见 test_full_tail_recovery.py），
所以 CI（只装 `[dev]`）也能跑。真正跑推理的版本见 test_full_engine_integration.py。

覆盖两件事：
1. **每行最多几个字在完整版上真正生效**——以前直接用上游的句子，行宽是摆设；
2. **时间来自上游的句级时间戳**（真实起止），不被重新按比例摊到整段音频上。
"""
from app.engine.funasr_full_engine import FunasrFullEngine
from app.schemas import JobOptions
from app.subtitle.segmentation import _visible_len


def _segs(res, max_chars=30, total_ms=10000):
    eng = FunasrFullEngine.__new__(FunasrFullEngine)   # 只用纯方法，不触发 __init__
    return eng._to_segments(res, JobOptions(max_chars=max_chars), total_ms)


LONG = "今天天气非常好我们一起去公园散步好不好这是一个本地语音转写的测试"


def test_line_width_limit_applies_to_upstream_sentences():
    """上游给的是整句（可能很长），我们按「每行最多几个字」再拆。"""
    res = [{
        "text": LONG + "。",
        "sentence_info": [{"text": LONG + "。", "start": 0, "end": 8000}],
    }]
    segs = _segs(res, max_chars=10)
    assert len(segs) >= 3
    for s in segs:
        # 标点算 0.5 宽，允许略微超出上限（标点跟随前句）
        assert _visible_len(s.text) <= 10 + 1, [s.text for s in segs]


def test_sentence_times_are_kept_not_reproportioned():
    """时间沿用上游给的真实起止，不按字数重新摊。"""
    res = [{
        "text": "第一句。第二句。",
        "sentence_info": [
            {"text": "第一句。", "start": 1200, "end": 3400},
            {"text": "第二句。", "start": 3400, "end": 5600},
        ],
    }]
    segs = _segs(res, max_chars=30, total_ms=9000)
    assert [(s.start_ms, s.end_ms) for s in segs] == [(1200, 3400), (3400, 5600)]


def test_speaker_label_survives_the_split():
    """说话人标签要跟着每一行走（拆行后不能丢 spk）。"""
    res = [{
        "text": LONG + "。",
        "sentence_info": [{"text": LONG + "。", "start": 0, "end": 8000, "spk": 1}],
    }]
    segs = _segs(res, max_chars=10)
    assert len(segs) >= 2
    assert all(s.speaker == "spk1" for s in segs)


def test_no_width_limit_keeps_upstream_sentences_merged_to_terminal():
    """max_chars=0：上游在逗号处断的句子要合并回整句（与 onnx 默认一致）。"""
    res = [{
        "text": "第一句，第二句。第三句。",
        "sentence_info": [
            {"text": "第一句，", "start": 0, "end": 1000},
            {"text": "第二句。", "start": 1000, "end": 2000},
            {"text": "第三句。", "start": 2000, "end": 3000},
        ],
    }]
    segs = _segs(res, max_chars=0, total_ms=3000)
    assert [s.text for s in segs] == ["第一句，第二句。", "第三句。"]


# ---- 上游 output_timestamp 的逐字时间戳（完整版 SenseVoice 靠它拿到真实时间）----
# 上游给 words（与正文同序的词/字）+ timestamp（每个词的起止毫秒）。


def test_per_token_timestamps_give_real_times():
    """有逐字时间戳就用它：时间来自 token，不再按字数比例摊到整段音频。"""
    text = "你好。世界。"
    words = list(text)
    ts = [[100 + 100 * i, 180 + 100 * i] for i in range(len(words))]
    res = [{"text": f"<|zh|><|NEUTRAL|><|Speech|><|withitn|>{text}", "words": words, "timestamp": ts}]
    segs = _segs(res, max_chars=30, total_ms=10000)
    assert [s.text for s in segs] == ["你好。", "世界。"]
    # 每条的 start = 该条第一个 token 的时间；end = 下一条第一个 token 的时间
    # （末条延伸到音频结束）——都是真时间，不按字数摊。
    assert (segs[0].start_ms, segs[0].end_ms) == (100, 400)
    assert (segs[1].start_ms, segs[1].end_ms) == (400, 10000)


def test_per_token_alignment_keeps_english_spaces():
    """英文的 words 是词级（不含空格），对齐后正文里的空格不能丢。"""
    text = "hello world"
    words = ["hello", "world"]
    ts = [[0, 500], [600, 1000]]
    res = [{"text": text, "words": words, "timestamp": ts}]
    segs = _segs(res, max_chars=30, total_ms=1000)
    assert [s.text for s in segs] == ["hello world"]
    assert segs[0].start_ms == 0


def test_per_token_alignment_failure_falls_back():
    """词对不上正文时不能瞎配：退回按比例分配那条路。"""
    res = [{
        "text": "你好世界",
        "words": ["不", "一", "样"],
        "timestamp": [[0, 100], [100, 200], [200, 300]],
    }]
    eng = FunasrFullEngine.__new__(FunasrFullEngine)
    assert eng._per_token_pieces(res[0]) is None

