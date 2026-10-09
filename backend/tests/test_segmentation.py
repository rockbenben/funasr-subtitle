"""自然语句切分 + 时间戳分配单测。"""
from app.schemas import Segment
from app.subtitle.segmentation import (
    enforce_monotonic,
    segment_span,
    segment_timed,
    split_sentences,
    strip_terminal_punct,
)


def test_strip_terminal_punct_keeps_commas_and_decimals():
    # 删句末终止标点，保留逗号、小数点、缩写内点
    assert strip_terminal_punct("Hello, world. How are you?") == "Hello, world How are you"
    assert strip_terminal_punct("它花了 3.14 元。") == "它花了 3.14 元"   # 小数点保留、句末。删
    assert strip_terminal_punct("the U.S. economy") == "the U.S economy"  # 句末位以外的点保留
    assert strip_terminal_punct("a; b; c.") == "a b c"                     # 分号/句末删
    assert strip_terminal_punct("no terminal here") == "no terminal here"


def test_enforce_monotonic_fixes_overlap_and_order():
    segs = [
        Segment(start_ms=1000, end_ms=1001, text="Hi."),
        Segment(start_ms=900, end_ms=2000, text="Yo."),  # 起点早于上一条终点
    ]
    out = enforce_monotonic(segs)
    assert out[0].end_ms <= out[1].start_ms      # 不重叠
    assert all(s.end_ms > s.start_ms for s in out)  # 每条非零时长
    assert out[1].start_ms == 1001               # 被钳到上一条 end


def test_timed_no_limit_keeps_clauses_together():
    # max_chars=0：只在句末标点断句，逗号不断
    pieces = [("今", 0), ("天", 50), ("，", 100), ("好", 150), ("。", 200),
              ("走", 300), ("吧", 350), ("。", 400)]
    segs = segment_timed(pieces, span_end_ms=500, max_chars=0)
    assert [s.text for s in segs] == ["今天，好。", "走吧。"]
    assert segs[0].start_ms == 0 and segs[-1].end_ms == 500


def test_timed_with_limit_splits_at_comma():
    pieces = [("今", 0), ("天", 50), ("，", 100), ("好", 150), ("。", 200)]
    segs = segment_timed(pieces, span_end_ms=300, max_chars=2)
    assert len(segs) == 2  # 超过 2 字后在逗号断


def test_timed_preserves_english_spaces():
    # piece 带词边界空格（来自 sentencepiece ▁ -> space），不应被吃掉
    pieces = [("Today", 0), (" the", 50), (" weather", 100), (".", 150)]
    segs = segment_timed(pieces, span_end_ms=200, max_chars=0)
    assert segs[0].text == "Today the weather."


def test_timed_english_period_space_splits():
    pieces = [("Hi", 0), (".", 100), (" Go", 200), (" on", 300), (".", 400)]
    segs = segment_timed(pieces, span_end_ms=500, max_chars=0)
    assert [s.text for s in segs] == ["Hi.", "Go on."]


def test_split_does_not_break_english_abbreviations():
    # e.g. / U.S. / Inc. 里的句点不应当作句末切句
    assert split_sentences("the U.S. economy grew.", max_chars=0) == [
        "the U.S. economy grew."
    ]
    assert split_sentences("see e.g. this example.", max_chars=0) == [
        "see e.g. this example."
    ]
    assert split_sentences("Acme Inc. shipped it.", max_chars=0) == [
        "Acme Inc. shipped it."
    ]
    # 真正的句末仍要切
    assert split_sentences("It works. We ship it.", max_chars=0) == [
        "It works.", "We ship it."
    ]


def test_split_long_cjk_latin_mix_no_oversized_line():
    # CJK 前缀 + 无空格长拉丁串：不应整体留成一条远超宽度的行
    s = "中" * 5 + "abcdefghijklmnopqrstuvwxyz0123" + "文" * 5
    pieces = split_sentences(s, max_chars=20)
    # 至少切成 2 段，且不是「单段全塞」
    assert len(pieces) >= 2
    # 拉丁长词本身不拆，但 CJK 边界要利用上，首段应是纯 CJK 前缀
    assert pieces[0] == "中" * 5


def test_single_letter_period_splits_when_next_lowercase():
    # 句末单字母 + 下一词小写 = 真正句末，应切
    assert split_sentences("the grade was a. then class ended.", max_chars=0) == [
        "the grade was a.", "then class ended."
    ]
    # 单字母 + 下一词大写(像首字母/名字) = 不切
    assert split_sentences("A. Smith arrived late.", max_chars=0) == [
        "A. Smith arrived late."
    ]


def test_timed_abbreviation_not_split():
    pieces = [("the", 0), (" U", 50), (".S", 100), (".", 150),
              (" economy", 200), (" grew", 250), (".", 300)]
    segs = segment_timed(pieces, span_end_ms=400, max_chars=0)
    assert [s.text for s in segs] == ["the U.S. economy grew."]


def test_sentence_final_abbrev_still_splits_when_next_capitalized():
    # 句末缩写 + 下一句首字母大写 -> 应当切句（不能因为 etc./Inc. 就并成一句）
    assert split_sentences("We use it etc. They agree.", max_chars=0) == [
        "We use it etc.", "They agree."
    ]
    assert split_sentences("Made by Acme Inc. They ship.", max_chars=0) == [
        "Made by Acme Inc.", "They ship."
    ]
    # 但缩写后接小写延续 -> 不切（e.g./U.S./延续）
    assert split_sentences("the U.S. economy grew fast.", max_chars=0) == [
        "the U.S. economy grew fast."
    ]
    assert split_sentences("see e.g. this and that.", max_chars=0) == [
        "see e.g. this and that."
    ]


def test_no_orphan_punctuation():
    # 纯标点碎片应并入上一句，不单独成行
    assert split_sentences("好几段。", max_chars=4) == ["好几段。"]
    segs = segment_timed([("好", 0), ("几", 50), ("段", 100), ("。", 150)], 200, max_chars=2)
    assert all(s.text.strip() != "。" for s in segs)
    assert "".join(s.text for s in segs) == "好几段。"


def test_split_long_english_no_punct_keeps_words():
    # 无标点的英文长句：按显示宽度在空格处断，绝不拆词
    parts = split_sentences("the quick brown fox jumps over the lazy dog", max_chars=8)
    assert len(parts) >= 2
    joined = " ".join(parts)
    assert joined.split() == "the quick brown fox jumps over the lazy dog".split()  # 单词完整


def test_width_english_longer_than_cjk():
    # 同一 max_chars 下，拉丁每行容纳的字符数约为 CJK 的 2 倍（显示宽度）
    en = split_sentences("alpha bravo charlie delta echo foxtrot golf hotel india", max_chars=10)
    # 10 宽 ≈ 20 拉丁字符/行；每行可见字符数应明显超过 10
    assert max(len(p.replace(" ", "")) for p in en) > 12


def test_split_long_unbreakable_word_not_cut():
    # 超长拉丁词且无空格：不从中间切（宁可超长）
    assert split_sentences("supercalifragilisticexpialidocious", max_chars=8) == [
        "supercalifragilisticexpialidocious"
    ]


def test_split_long_cjk_no_punct_char_cut():
    parts = split_sentences("一二三四五六七八九十", max_chars=4)
    assert parts == ["一二三四", "五六七八", "九十"]


def test_timed_english_cap_breaks_at_word_boundary():
    pieces = [("the", 0), (" quick", 50), (" brown", 100), (" fox", 150)]
    segs = segment_timed(pieces, 200, max_chars=8)
    for s in segs:  # 不拆词
        for w in s.text.split():
            assert w in {"the", "quick", "brown", "fox"}


def test_timed_charlevel_no_empty_terminal_bug():
    # 字符级 pieces（含纯空白）：空白 piece 的 last="" 不能被当作句末标点
    # （"" in 任意串都为 True 的坑）；应只在 "." 处断，且第二句用真实时间。
    pieces = [("H", 100), ("i", 100), (".", 100), (" ", 100), ("G", 900), ("o", 900)]
    segs = segment_timed(pieces, 1000, max_chars=0)
    assert [s.text for s in segs] == ["Hi.", "Go"]
    assert segs[1].start_ms == 900  # 真实时间（非前一 piece/比例）


def test_timed_decimal_not_split():
    # '.' 后无空格（小数/缩写）不断句
    pieces = [("3", 0), (".", 50), ("14", 100), (" pies", 150)]
    segs = segment_timed(pieces, span_end_ms=200, max_chars=0)
    assert len(segs) == 1 and segs[0].text == "3.14 pies"


def test_split_at_sentence_punctuation():
    parts = split_sentences("今天天气非常好，我们去公园吧。这是一个测试。", max_chars=0)
    assert parts == ["今天天气非常好，我们去公园吧。", "这是一个测试。"]


def test_split_long_sentence_at_comma():
    s = "甲乙丙丁戊己庚辛，壬癸子丑寅卯辰巳午未申酉戌亥再加几个字凑长一点继续"
    parts = split_sentences(s, max_chars=12)
    assert len(parts) >= 2
    assert all(len(p.replace(" ", "")) <= 12 + 2 for p in parts)  # 容许标点略超


def test_segment_span_allocates_time_proportionally():
    segs = segment_span("今天天气非常好。这是一个本地语音转写的测试。", 0, 10000, max_chars=0)
    assert len(segs) == 2
    # 连续、覆盖整段
    assert segs[0].start_ms == 0
    assert segs[-1].end_ms == 10000
    assert segs[0].end_ms == segs[1].start_ms
    # 第二句更长 -> 占用更多时间
    assert (segs[1].end_ms - segs[1].start_ms) > (segs[0].end_ms - segs[0].start_ms)


def test_single_sentence_keeps_span():
    segs = segment_span("就一句话", 1000, 3000)
    assert len(segs) == 1
    assert (segs[0].start_ms, segs[0].end_ms) == (1000, 3000)


def test_empty_text():
    assert segment_span("   ", 0, 1000) == []
