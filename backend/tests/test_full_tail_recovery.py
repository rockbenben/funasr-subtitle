"""上游 funasr #3754 的缓解回归测试。

背景：funasr 的 `timestamp_sentence()` 只在遇到终止标点时才 flush 一句，于是**末尾那段
没有终止标点的文本会被整段丢弃**（modelscope/FunASR#3754，截至 1.4.16 仍未发版）。
表现：说话人说一半被打断 / 录音到点结束时，字幕尾部凭空少一截。

夹具按**实测的真实输出形状**构造：
- r0["text"] 与 sentence_info 拼接都是带标点的同一段文本；
- 但上游丢尾巴时，text 里多出来的那截**没有**终止标点，于是 sentence_info 少了它；
- 比较覆盖度必须忽略标点，否则 ct-punc 给 sentence_info 补的句号会被当成「多了内容」。
"""
from app.engine.funasr_full_engine import (
    FunasrFullEngine,
    _nospace,
    _skeleton,
    _tail_after,
)
from app.schemas import JobOptions


def _segs(res, max_chars=30, total_ms=3000):
    eng = FunasrFullEngine.__new__(FunasrFullEngine)   # 只用纯方法，不触发 __init__
    return eng._to_segments(res, JobOptions(max_chars=max_chars), total_ms)


# ---- 上游 bug 的形状：text 完整，sentence_info 少了无标点的尾巴 ----

def test_unpunctuated_tail_is_recovered():
    res = [{
        "text": "你好世界",                       # 尾巴「世界」没有终止标点
        "sentence_info": [{"text": "你好。", "start": 0, "end": 200}],
    }]
    segs = _segs(res)
    assert [s.text for s in segs] == ["你好。", "世界"]
    # sentence_info 里的句号是 ct-punc 补的，所以拼接结果带标点；关键是「世界」没丢
    assert "".join(s.text for s in segs) == "你好。世界"
    assert segs[-1].start_ms == 200, "尾巴应从上一条的结束时间接上"
    assert segs[-1].end_ms == 3000, "尾巴延伸到音频结束"


def test_tail_recovery_keeps_speaker():
    res = [{
        "text": "第一句。第二句没说完",
        "sentence_info": [{"text": "第一句。", "start": 0, "end": 200, "spk": 1}],
    }]
    segs = _segs(res)
    assert len(segs) == 2
    assert segs[0].speaker == "spk1"
    assert segs[1].speaker == "spk1", "尾巴应继承同一说话人"
    assert segs[1].text == "第二句没说完"


def test_tail_stays_separate_when_previous_sentence_is_punctuated():
    """max_chars=0 也**不会**把尾巴并回上一句。

    `_merge_to_terminal` 只向后合并连续的「无标点」碎片；上一句以句号收尾时已经断开了，
    尾巴只能自成一条。这是既有行为，缓解逻辑不改变它——重点是「不丢字」。
    """
    res = [{
        "text": "你好世界",
        "sentence_info": [{"text": "你好。", "start": 0, "end": 200}],
    }]
    segs = _segs(res, max_chars=0)
    assert [s.text for s in segs] == ["你好。", "世界"]


def test_unpunctuated_runs_merge_forward():
    """max_chars=0：连续的「无标点」碎片向前合并（既有行为，缓解逻辑不应改变它）。"""
    res = [{
        "text": "你好。世界啊呢",                  # text 与 sentence_info 一致，不触发补尾巴
        "sentence_info": [
            {"text": "你好。", "start": 0, "end": 200},
            {"text": "世界", "start": 300, "end": 400},
            {"text": "啊", "start": 500, "end": 600},
            {"text": "呢", "start": 700, "end": 800},
        ],
    }]
    segs = _segs(res, max_chars=0)
    assert [s.text for s in segs] == ["你好。", "世界啊呢"]


# ---- 正常路径不得被误伤 ----

def test_complete_sentence_info_untouched():
    """覆盖完整时一个字都不该多补，时间戳也不该被改写。"""
    text = "今天天气非常好，我们一起去公园散步，好不好？这是一个测试。"
    res = [{
        "text": text,
        "sentence_info": [
            {"text": "今天天气非常好，", "start": 170, "end": 1950},
            {"text": "我们一起去公园散步，", "start": 2530, "end": 4630},
            {"text": "好不好？", "start": 4650, "end": 5330},
            {"text": "这是一个测试。", "start": 6370, "end": 9745},
        ],
    }]
    segs = _segs(res, total_ms=10320)
    assert len(segs) == 4
    assert "".join(s.text for s in segs) == text
    assert segs[-1].end_ms == 9745, "已有完整时间戳时不应被改写"


def test_punctuation_only_difference_is_not_treated_as_tail():
    """ct-punc 给 sentence_info 补了句号、text 没有 -> 只是标点差异，不是丢内容。"""
    res = [{
        "text": "今天天气不错",
        "sentence_info": [{"text": "今天天气不错。", "start": 0, "end": 500}],
    }]
    segs = _segs(res)
    assert len(segs) == 1, "标点差异不应触发补尾巴"
    assert segs[0].text == "今天天气不错。"


def test_middle_gap_is_not_guessed():
    """丢的不是尾巴（中间缺段）时不猜，原样保留交给兜底路径。"""
    res = [{
        "text": "甲乙丙",                          # 中间缺「乙」：不是前缀关系
        "sentence_info": [{"text": "甲丙", "start": 0, "end": 200}],
    }]
    segs = _segs(res)
    assert [s.text for s in segs] == ["甲丙"]


def test_no_room_for_tail_is_skipped():
    """上一条已经延伸到音频结束 -> 不硬塞，避免制造越界/乱序。"""
    res = [{
        "text": "你好世界",
        "sentence_info": [{"text": "你好。", "start": 0, "end": 3000}],
    }]
    segs = _segs(res, total_ms=3000)
    assert [s.text for s in segs] == ["你好。"]


def test_missing_text_key_is_safe():
    res = [{"sentence_info": [{"text": "只有这一句。", "start": 0, "end": 500}]}]
    segs = _segs(res)
    assert [s.text for s in segs] == ["只有这一句。"]


# ---- 辅助函数 ----

def test_skeleton_drops_punctuation_and_space():
    assert _skeleton("你好，世界！ ok?") == "你好世界ok"
    assert _nospace(" a b\tc\n") == "abc"


def test_tail_after():
    assert _tail_after("你好世界", 2) == "世界"
    assert _tail_after("你好，世界", 2) == "世界"     # 标点不算骨架字符
    assert _tail_after("你好世界", 4) == ""          # 全覆盖
    assert _tail_after("你好世界", 9) == ""          # 超范围
