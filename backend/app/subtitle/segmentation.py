"""把识别文本切成符合专业字幕规范的逐句字幕。

参考 Netflix/BBC 字幕规范的做法（不引重依赖，规则直接实现）：
- **每行宽度** max_chars 以「显示宽度」计：中日韩字符=1，拉丁/数字等=0.5，
  所以一个 cap 同时合理约束中英（默认 30 ≈ 中文 30 字 / 英文 ~60 字）。
- **断点优先级**：句末标点 > 子句标点(，、;) > 词边界(空格) > CJK 字；拉丁词不中途断。
- **单条时长**上限 MAX_CUE_MS（有逐 token 时间时生效）。
- 时间戳：SenseVoice 由 CTC 帧位置给逐 token 时间；无标点文本补标点后按比例分配。

纯函数，无外部依赖，便于单测。
"""
from __future__ import annotations

import re

from ..config import MAX_CUE_MS
from ..schemas import Segment

# 句子终止标点（在其后切句，标点跟随前句）
_TERMINALS = "。！？!?…"
# 次级停顿标点（仅当一句过长时，在此进一步切分以便阅读）
_SOFT = "，,、；;：:"
# 实义字符（中日韩 + 字母数字）；没有实义字符的片段视为「纯标点」，应并入上一句
_WORD = re.compile(r"[\w一-鿿぀-ヿ가-힯]")
# 中日韩表意字符（可在任意字处断行；拉丁词不可中途断）
_CJK = re.compile(r"[一-鿿぀-ヿ가-힯]")


def _is_punct_only(s: str) -> bool:
    return _WORD.search(s) is None


# 头衔/引用类缩写：几乎不作句末（其后多为人名/延续），恒不切
_TITLE_ABBREVS = {
    "mr", "mrs", "ms", "dr", "prof", "st", "sr", "jr", "messrs", "mt", "rev",
    "hon", "capt", "col", "gen", "sgt", "lt", "gov", "sen", "vs", "no", "vol",
    "fig", "dept", "approx", "ave", "blvd", "est", "ph",
}
# 既可句中也常作句末的缩写：仅当其后是小写延续时才不切（大写=新句，照切）
_TERMINAL_ABBREVS = {"inc", "ltd", "co", "corp", "etc"}
_ABBREV_TAIL = re.compile(r"([A-Za-z][A-Za-z.]*)\.$")


# 句末终止标点（「按停顿」模式删除这些；英文句号另行按位置判断）
_STRIP_TERMINALS = frozenset("。！？!?…；;")


def strip_terminal_punct(text: str) -> str:
    """删句末终止标点，保留逗号/冒号/小数点/缩写内点。

    用于「按停顿」标点模式（英文默认）：避免不可靠的句末标点（错位的句号会把一句切碎）。
    英文句号仅当其后是空格/结尾时才算句末删除——`3.14`、`U.S` 里的点（后面非空格）保留。
    """
    out: list[str] = []
    n = len(text)
    for i, ch in enumerate(text):
        if ch in _STRIP_TERMINALS:
            continue
        if ch == ".":
            after = text[i + 1] if i + 1 < n else ""
            if after == "" or after.isspace():
                continue
        out.append(ch)
    return "".join(out)


def _ends_with_abbrev(text: str, next_is_lower: bool) -> bool:
    """text 以「疑似缩写 + 句点」结尾、且不应在此切句时返回 True。

    - 含内部点(U.S./e.g.)、单字母(J.)、头衔(Mr./Dr.)：恒不切。
    - 一般缩写(Inc./etc./Co.)：仅当下一词小写(延续)才不切；下一词大写视为新句，照切。
    只看末尾 ~40 字符，避免对超长无空格串做 O(n^2) 回溯。
    """
    m = _ABBREV_TAIL.search(text[-40:].rstrip())
    if not m:
        return False
    word = m.group(1)
    if "." in word:  # 含内部点：U.S / e.g / i.e
        return True
    if len(word) == 1:
        # 单字母：仅当下一词大写(像 J. R. / A. Smith 这类首字母)才不切；
        # 下一词小写说明是真正的句末单字母(如 "graded a. then ...")，照切。
        return not next_is_lower
    w = word.lower()
    if w in _TITLE_ABBREVS:
        return True
    if w in _TERMINAL_ABBREVS:
        return next_is_lower  # 下一句小写=延续(不切)；大写=新句(切)
    return False

# 单条字幕最长时长（毫秒）：MAX_CUE_MS 自 config 导入，专业规范一般 ≤7s
# （有逐 token 时间时才能精确执行）；可用环境变量 FUNASR_SUBTITLE_MAX_CUE_MS 覆盖。


def _visible_len(s: str) -> float:
    """显示宽度：中日韩字符算 1，拉丁/数字/其它非空白算 0.5（空白不计）。"""
    w = 0.0
    for ch in s:
        if ch.isspace():
            continue
        w += 1.0 if _CJK.match(ch) else 0.5
    return w


def split_sentences(text: str, max_chars: int = 20) -> list[str]:
    """切成自然语句；过长的句子再在次级标点处贪心切分到 <= max_chars。"""
    text = text.strip()
    if not text:
        return []

    # 1) 在终止标点后切句（保留标点）；英文 '.'/';' 仅当其后空格/结尾才算句末（避开小数/缩写）
    sentences: list[str] = []
    buf = ""
    nlen = len(text)
    for i, ch in enumerate(text):
        buf += ch
        nxt = text[i + 1] if i + 1 < nlen else ""
        if ch in ".;" and (nxt == "" or nxt.isspace()):
            # 下一个有意义字符（跳过空白）是否小写，用于判断缩写后是延续还是新句
            j = i + 1
            while j < nlen and text[j].isspace():
                j += 1
            next_is_lower = j < nlen and text[j].islower()
            soft = not _ends_with_abbrev(buf, next_is_lower)
        else:
            soft = False
        is_term = ch in _TERMINALS or soft
        if is_term:
            s = buf.strip()
            if s:
                sentences.append(s)
            buf = ""
    if buf.strip():
        sentences.append(buf.strip())

    if max_chars > 0:
        # 2) 过长句子在次级标点处再切（尽量靠近 max_chars），仍过长则硬切
        split: list[str] = []
        for s in sentences:
            split.extend(_split_long(s, max_chars))
        sentences = split

    # 3) 纯标点的碎片并入上一句，避免「。」单独成行
    merged: list[str] = []
    for s in sentences:
        if merged and _is_punct_only(s):
            merged[-1] += s
        else:
            merged.append(s)
    return merged


def _split_long(s: str, max_chars: int) -> list[str]:
    """把过长的一句按显示宽度 max_chars 贪心切；断点优先 次级标点>空格>CJK字。"""
    if _visible_len(s) <= max_chars:
        return [s]
    pieces: list[str] = []
    start = 0
    n = len(s)
    while start < n:
        while start < n and s[start] == " ":  # 跳过前导空格
            start += 1
        if start >= n:
            break
        # 从 start 累积显示宽度到 max_chars，得到窗口末位 end
        end = start
        w = 0.0
        while end < n and w < max_chars:
            if not s[end].isspace():
                w += 1.0 if _CJK.match(s[end]) else 0.5
            end += 1
        if end >= n:
            tail = s[start:n].strip()
            if tail:
                pieces.append(tail)
            break
        # 切点优先级：窗口内最后的 次级标点 > 空格(词边界) > CJK 字边界 > 拉丁扩到下一空格
        cut = -1
        for i in range(end - 1, start - 1, -1):
            if s[i] in _SOFT:
                cut = i + 1
                break
        if cut == -1:
            for i in range(end - 1, start - 1, -1):
                if s[i] == " ":
                    cut = i + 1  # 空格后切，不拆英文单词
                    break
        if cut == -1 and _CJK.match(s[end - 1]):
            cut = end  # CJK 可按字断
        if cut == -1:
            # 窗口末位是拉丁、且无标点/空格可断：退回窗口内最后一个 CJK 字处切，
            # 避免「CJK+无空格长拉丁串」被整体留成一条超宽行。
            for i in range(end - 1, start - 1, -1):
                if _CJK.match(s[i]):
                    cut = i + 1
                    break
        if cut == -1 or cut <= start:
            nxt = s.find(" ", end)  # 拉丁长词无空格：扩到下一个空格，避免拆词
            cut = nxt + 1 if nxt != -1 else n
        piece = s[start:cut].strip()
        if piece:
            pieces.append(piece)
        start = cut
    return pieces or [s]


def segment_timed(
    pieces: list[tuple[str, int]],
    span_end_ms: int,
    max_chars: int = 20,
) -> list[Segment]:
    """用**真实 token 时间**切句（SenseVoice CTC 帧位置推出的时间戳）。

    pieces: [(token文本, 绝对时间ms), ...]，按时间升序。
    在句子终止标点处断句；句子过长则在次级标点处断。
    每句 start=首 token 时间，end=下一句首 token 时间（末句=span_end_ms）。
    """
    pieces = [(t, ms) for t, ms in pieces if t]
    if not pieces:
        return []

    sentences: list[tuple[str, int]] = []  # (text, start_ms)
    buf = ""
    buf_start: int | None = None
    vis = 0
    n = len(pieces)
    for idx, (text, ms) in enumerate(pieces):
        if buf_start is None and text.strip():  # 起始以首个非空白 token 的时间为准
            buf_start = ms
        buf += text
        vis += _visible_len(text)
        last = text.strip()[-1:] if text.strip() else ""
        nxt = pieces[idx + 1][0] if idx + 1 < n else None
        next_starts_space = nxt is None or nxt[:1].isspace()
        _nxt_sig = nxt.lstrip()[:1] if nxt else ""  # 下一片首个有意义字符
        # 句末标点恒断；英文 '.'/';' 仅当其后是空格/结尾时才断（避开小数、缩写）。
        # 注意：last 可能为空串（纯空白 piece），而 "" in 任何字符串都为 True，必须先判非空。
        hard = bool(last) and last in _TERMINALS
        soft_period = (bool(last) and last in ".;" and next_starts_space
                       and not _ends_with_abbrev(buf, _nxt_sig.islower()))
        # 超过 max_chars 时在「安全边界」断：次级标点 / 词边界(下一片以空格起) / CJK 字。
        # 拉丁词中途（last 是字母、下一片非空格）不断，避免拆词；
        # 若下一片是标点，则不在 CJK 字处断（让标点跟在本行末，避免标点跑到下一行行首）。
        nxt_head = nxt.lstrip()[:1] if nxt else ""
        next_is_punct = bool(nxt_head) and nxt_head in (_SOFT + _TERMINALS)
        boundary = (
            (bool(last) and last in _SOFT)
            or next_starts_space
            or (bool(_CJK.match(last)) and not next_is_punct)
        )
        too_long = max_chars > 0 and vis >= max_chars and boundary
        # 单条时长超上限也在安全边界断（专业规范 ≤7s）
        over_dur = buf_start is not None and (ms - buf_start) >= MAX_CUE_MS and boundary
        if hard or soft_period or too_long or over_dur:
            s = buf.strip()
            if s:
                sentences.append((s, buf_start))
            buf, buf_start, vis = "", None, 0
    if buf.strip():
        sentences.append((buf.strip(), buf_start or 0))

    # 纯标点碎片并入上一句（保留上一句的 start），避免「。」单独成行
    merged: list[tuple[str, int]] = []
    for txt, st in sentences:
        if merged and _is_punct_only(txt):
            merged[-1] = (merged[-1][0] + txt, merged[-1][1])
        else:
            merged.append((txt, st))
    sentences = merged

    segs: list[Segment] = []
    for i, (txt, st) in enumerate(sentences):
        en = sentences[i + 1][1] if i + 1 < len(sentences) else span_end_ms
        if en <= st:
            en = st + 1
        segs.append(Segment(start_ms=int(st), end_ms=int(en), text=txt))
    return segs


def enforce_monotonic(segs: list[Segment]) -> list[Segment]:
    """保证字幕条按时间单调、不重叠：start 不早于上一条 end，end 至少比 start 大 1ms。

    防御性后处理：无论各段时间戳怎么算出来（跨 VAD 段、比例分配、标点对齐误差），
    最终交给播放器/导出的 cue 必须严格递增不重叠，否则 SRT/VTT 会乱序或被拒。
    """
    prev_end: int | None = None
    for s in segs:
        if prev_end is not None and s.start_ms < prev_end:
            s.start_ms = prev_end
        if s.end_ms <= s.start_ms:
            s.end_ms = s.start_ms + 1
        prev_end = s.end_ms
    return segs


def segment_span(
    text: str,
    start_ms: int,
    end_ms: int,
    max_chars: int = 20,
) -> list[Segment]:
    """把一个 VAD 语音段的文本切成逐句字幕，按字符数比例分配时间。"""
    pieces = split_sentences(text, max_chars=max_chars)
    if not pieces:
        return []
    if len(pieces) == 1:
        return [Segment(start_ms=start_ms, end_ms=end_ms, text=pieces[0])]

    weights = [max(1, _visible_len(p)) for p in pieces]
    total = sum(weights)
    span = max(0, end_ms - start_ms)

    segs: list[Segment] = []
    acc = 0
    for p, w in zip(pieces, weights, strict=True):  # 同源派生，长度必然一致
        s = start_ms + round(span * acc / total)
        acc += w
        e = start_ms + round(span * acc / total)
        if e <= s:
            e = s + 1
        segs.append(Segment(start_ms=s, end_ms=e, text=p))
    # 末段对齐到 end_ms
    segs[-1].end_ms = max(segs[-1].start_ms + 1, end_ms)
    return segs
