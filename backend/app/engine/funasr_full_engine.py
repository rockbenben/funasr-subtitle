"""完整 funasr（torch）后端的 ASREngine 实现（"full" 构建变体 / CUDA 便携包用）。

与 funasr-onnx 后端的区别：
- 用 `funasr.AutoModel` 跑 VAD + ASR + (punc) 一体管线，device 可选 cuda（真显卡加速）。
- 时间戳优先用上游的**逐字时间戳**（`generate(output_timestamp=True)`，SenseVoice 也能给——
  它没有 timestamp 头，上游按帧反推）；其次才是 `sentence_info`（句级 start/end/text/spk，
  带说话人时只有它有）。两条都拿不到才按标点切句 + 比例时间兜底。
- 上游不管行长（那是字幕需求），**「每行最多几个字」由我们自己补上**——别把上游的句子
  直接当最终 cue。
- 模型下载走 modelscope，缓存根 = config.model_cache_dir()（默认
  %LOCALAPPDATA%\\funasr-subtitle\\models，与 onnx 后端共用一份；用户设了
  MODELSCOPE_CACHE 就跟随它）。
- 完整版用的是 **pt 权重**（SenseVoice ≈ 0.9GB；Paraformer 还要带 ct-punc ≈ 2.1GB），
  和 onnx 后端那批仓库不是一回事——对应关系见 models/registry.py 的 full_repo。

注意：本文件只在装了 torch + funasr 的 "full" 环境里被 import（build_engine 按 backend 分派）。
"""
from __future__ import annotations

import os
import re

import numpy as np

from ..config import MIN_PAUSE_MS, model_cache_dir
from ..models import ModelManager, get_model
from ..schemas import JobOptions, Segment, Word
from ..subtitle import (
    attach_words,
    enforce_monotonic,
    segment_span,
    segment_timed,
    strip_terminal_punct,
)
from ..subtitle.segmentation import _SOFT, _TERMINALS
from .base import EngineOptions, ProgressCallback

_CJK_RE = re.compile(r"[一-鿿぀-ヿ가-힯]")

# 判断「一个字」时跳过的标点（上游的逐词时间只覆盖实义字符）
_PUNCT_CHARS = _SOFT + _TERMINALS + ",.!?"

_SPECIAL = re.compile(r"<\|[^|>]*\|>")
_VALID_LANGS = {"auto", "zh", "yue", "en", "ja", "ko"}


def _merge_to_terminal(raw: list[list]) -> list[list]:
    """合并相邻子句直到句末标点，得到自然语句（start=首句起，end=末句止）。
    换说话人时先收尾，不跨说话人合并。逐词时间（末位）也跟着拼。"""
    out: list[list] = []
    cur: list | None = None
    for st, en, tx, spk, times in raw:
        if cur is not None and cur[3] != spk:  # 换人 -> 先收尾
            out.append(cur)
            cur = None
        if cur is None:
            cur = [st, en, tx, spk, list(times) if times else None]
        else:
            cur[1] = en
            # 拉丁词之间补空格，避免「the cat」+「sat」拼成「the catsat」；CJK 不补
            if (cur[2][-1:].isascii() and cur[2][-1:].isalnum()
                    and tx[:1].isascii() and tx[:1].isalnum()):
                cur[2] += " " + tx
            else:
                cur[2] += tx
            if cur[4] and times:
                cur[4] = cur[4] + list(times)
            elif times:
                cur[4] = list(times)
        if cur[2].rstrip()[-1:] in _TERMINALS:
            out.append(cur)
            cur = None
    if cur is not None:
        out.append(cur)
    return out


def _nospace(s: str) -> str:
    """去掉所有空白，用于「覆盖是否完整」的比较（忽略空格差异）。"""
    return "".join(s.split())


# 「骨架字符」= 字母 / 数字 / 中日韩，去掉标点与空白。
# ct-punc 会给 sentence_info 补句号，而 r0["text"] 不一定同步，因此比对覆盖度时必须
# 忽略标点，否则「上游补了个句号」会被误判成「多了内容」。
_SKELETON_RE = re.compile(r"[^\W_]", re.UNICODE)


def _skeleton(s: str) -> str:
    """只保留实义字符，用于跨标点比较文本覆盖度。"""
    return "".join(_SKELETON_RE.findall(s))


def _tail_after(text: str, covered: int) -> str:
    """取 text 中「前 covered 个骨架字符之后」的剩余部分。

    covered 通常小于骨架长度（上游丢了句尾），此时返回丢掉的那截。
    若 covered 已覆盖全部骨架字符则返回空串。
    """
    seen = 0
    for i, ch in enumerate(text):
        if _SKELETON_RE.match(ch):
            if seen == covered:
                return text[i:]
            seen += 1
    return ""


class FunasrFullEngine:
    SAMPLE_RATE = 16_000

    def __init__(self, opts: EngineOptions, manager=None) -> None:  # manager 仅为签名兼容
        self.opts = opts
        spec = get_model(opts.model_id)
        self._full_model = spec.full_model or "iic/SenseVoiceSmall"
        self._builtin_punc = spec.builtin_punc
        self._model = None
        self._loaded = False
        self.max_chars = 30
        self.gpu_active = False

    def load(self) -> None:
        if self._loaded:
            return
        # 必须与 ModelManager 的下载目录一致：两者都对着 model_cache_dir()（用户设了
        # MODELSCOPE_CACHE 就跟随它），否则界面「下载模型」和 funasr 会各用一套缓存。
        os.environ["MODELSCOPE_CACHE"] = self.opts.models_root or str(model_cache_dir())
        import torch
        from funasr import AutoModel

        # full 是「GPU 构建」：有 CUDA 就用（前端已隐藏 GPU 开关，不依赖 use_gpu）。
        # 设 FUNASR_SUBTITLE_FORCE_CPU=1 可强制 CPU（无卡机/排障）。
        force_cpu = os.environ.get("FUNASR_SUBTITLE_FORCE_CPU") == "1"
        use_cuda = (not force_cpu) and torch.cuda.is_available()
        self.gpu_active = use_cuda
        kwargs = dict(
            model=self._full_model,
            vad_model="fsmn-vad",
            device="cuda:0" if use_cuda else "cpu",
            disable_update=True,
            disable_pbar=True,
            log_level="ERROR",
        )
        # 说话人分离：仅 Paraformer 系（非 builtin_punc，能出 sentence_info）+ 勾选时启用
        self._diar = bool(self.opts.diarization) and not self._builtin_punc
        if not self._builtin_punc:
            kwargs["punc_model"] = "ct-punc"  # SenseVoice 自带标点则不加
            if self._diar:
                # cam++ 说话人分离：不在下载面板的预置清单里，由 funasr 在这里按需拉取
                # （体积不大，且只有勾了这个开关才用得上）。
                kwargs["spk_model"] = "cam++"
        # 与「下载模型」共用按 model_id 的锁：面板还在下 pt 模型时，这里等它下完再加载，
        # 避免两个 snapshot_download 并发写同一个平铺目录。
        with ModelManager.download_lock(self.opts.model_id):
            self._model = AutoModel(**kwargs)
        self._loaded = True

    def transcribe(
        self,
        wav: np.ndarray,
        sample_rate: int,
        options: JobOptions,
        progress: ProgressCallback | None = None,
        cancel: object | None = None,
    ) -> list[Segment]:
        if not self._loaded:
            self.load()
        # full 后端是单次 generate() 一体管线，无法在内部安全打断；取消在进入前/解码阶段生效。
        if cancel is not None and cancel.is_set():
            return []
        if sample_rate != self.SAMPLE_RATE:
            raise ValueError(f"expected {self.SAMPLE_RATE} Hz, got {sample_rate}")
        wav = np.asarray(wav, dtype=np.float32)

        if progress:
            progress("asr", 10.0)
        lang = options.language if options.language in _VALID_LANGS else "auto"
        gen = dict(input=wav, cache={}, language=lang, use_itn=options.itn, batch_size_s=300)
        # sentence_timestamp：两种模型都要开。
        # - 带时间戳头的（Paraformer）：上游按标点切句并给每句真实起止时间；
        # - 不带的（SenseVoice）：上游退化成「VAD 段 = 一句」（auto_model._vad_segment_sentences），
        #   也就是说话停下来的地方自然断句——这正是我们要的，比「整段按字数比例分时间」准得多。
        #   （实测 funasr 1.4.16 对 SenseVoice 也能开，不会报错。）
        gen["sentence_timestamp"] = True
        # 逐字时间戳（上游 output_timestamp）：SenseVoice 也能给逐字起止时间（上游按 60ms/帧
        # 算，和 onnx 路径实测的帧率一致）。比「整段按字数比例摊时间」准一个档——
        # 上游 SenseVoice 没有 timestamp 头，之前只能退化成「VAD 段 = 一句」。
        # 注意：开了它之后上游对「没有 punc 模型」的句子切分会跳过（要求 punc_model），
        # 所以 sentence_info 会变空——我们改用自己的逐字路径拼句，见 _per_token_pieces。
        if self._builtin_punc:
            gen["output_timestamp"] = True
        # 停顿断句（issue #5）：上游 VAD 的静音阈值。传显式值会关掉上游的动态静音调度，
        # 改成固定阈值——静默超过这个长度就断成新的语音段（上游参数，不动模型逻辑）。
        if options.pause_ms > 0:
            gen["max_end_silence_time"] = max(options.pause_ms, MIN_PAUSE_MS)
        if options.hotwords:
            gen["hotword"] = options.hotwords
        # 英文用上游的英文断句规则（timestamp_sentence_en）：实测与中文规则的差别就是
        # 标点宽度——中文规则往英文里插全角「，」「。」，英文规则插半角 ", " / ". "。
        # 语言「自动」时保持上游默认（中文规则），因为上游这个开关是整段文件级的。
        if lang == "en":
            gen["en_post_proc"] = True
        res = self._model.generate(**gen)
        if progress:
            progress("assembling", 100.0)

        total_ms = int(len(wav) / self.SAMPLE_RATE * 1000)
        segs = enforce_monotonic(self._to_segments(res, options, total_ms))
        # 标点模式：off=都去句末标点；auto=英文(无 CJK)去、中文保留；on=保留。
        mode = getattr(options, "punctuation", "auto")
        if mode != "on" and segs:
            is_cjk = bool(_CJK_RE.search("".join(s.text for s in segs)))
            if mode == "off" or (mode == "auto" and not is_cjk):
                for s in segs:
                    s.text = strip_terminal_punct(s.text)
        return segs

    @staticmethod
    def _recover_dropped_tail(raw: list[list], r0, total_ms: int) -> list[list]:
        """补回 funasr 在句级时间戳里丢掉的无标点句尾（上游 #3754，至今未发版）。

        funasr 的 `timestamp_sentence()` 只在遇到终止标点时才 flush 一句，于是**末尾那段
        没有终止标点的文本会被整段丢弃**——说话人说一半被打断、录音到点结束都很常见，
        表现为字幕尾部凭空少一截，而 `r0["text"]` 里其实还在。

        这里比对「sentence_info 拼出来的文本」和「整段文本」的实义字符（忽略标点，因为
        ct-punc 会给前者补句号），确认确实是前缀关系（丢的是尾巴而不是中间），再把剩下
        那截按「上一条结束 -> 音频结束」的时间范围补回去，沿用上一条的说话人标签。
        时间戳刻意不从 `timestamp` 对齐：实测该数组按「非标点 token」给，和字符数对不上，
        硬对齐反而更容易错。宁可时间范围略宽，也不能把字丢了。
        对不上的情况一律原样返回，不猜、不动原有分段。
        """
        if not isinstance(r0, dict) or not raw:
            return raw
        full_text = _SPECIAL.sub("", str(r0.get("text", ""))).strip()
        joined = "".join(t for _st, _en, t, _spk, _ts in raw)
        covered, total = len(_skeleton(joined)), len(_skeleton(full_text))
        if total <= covered:
            return raw  # 覆盖完整，没有尾巴
        if not _skeleton(full_text).startswith(_skeleton(joined)):
            return raw  # 不是前缀关系（丢的不是尾巴）-> 交给原有兜底，别瞎猜
        tail = _tail_after(full_text, covered)
        if not _skeleton(tail):
            return raw
        last_end = int(raw[-1][1])
        if last_end >= total_ms:  # 时间上没地方放，硬塞会制造乱序
            return raw
        raw.append([last_end, total_ms, tail, raw[-1][3], None])
        return raw

    @staticmethod
    def _per_token_pieces(r0) -> list[tuple[str, int]] | None:
        """从上游的逐字时间戳还原「带时间的 token」，对不上就返回 None。

        上游 `output_timestamp=True` 会给出 `words`（与正文同序的词/字）和
        `timestamp`（每个词的起止毫秒）。这里按「把 words 依次贴回正文」的方式对齐：
        中文是字级（一个词一个字），英文是词级（词里含空格），所以不能按字符数硬配。

        对不上（上游没给、时间戳个数不符、词在正文里找不到）就返回 None，
        交给 sentence_info / 比例分配那条路兜底。
        """
        if not isinstance(r0, dict):
            return None
        ts = r0.get("timestamp")
        words = r0.get("words")
        text = _SPECIAL.sub("", str(r0.get("text", ""))).strip()
        if not ts or not words or len(ts) != len(words) or not text:
            return None
        pieces: list[tuple[str, int]] = []
        pos = 0
        for w, span in zip(words, ts, strict=True):
            w = str(w)
            if not w:
                return None
            idx = text.find(w, pos)
            if idx < 0:
                return None
            pieces.append((text[pos:idx + len(w)], int(span[0])))
            pos = idx + len(w)
        tail = text[pos:]
        if tail:
            # 末尾多出来的（多半是标点）跟着最后一段走
            if not pieces:
                return None
            last_text, last_t = pieces[-1]
            pieces[-1] = (last_text + tail, last_t)
        return pieces

    @staticmethod
    def _words_from_times(text: str, times) -> list[Word] | None:
        """把上游 sentence_info 里的逐词时间对齐到句子正文。

        上游给的是 punc 模型的 token 数：中文是字级、英文是词级。
        两种都对不上就返回 None（没有逐字时间戳，而不是给错的）。
        """
        if not times:
            return None
        chars = [c for c in text if not c.isspace() and c not in _PUNCT_CHARS]
        if len(chars) == len(times):
            units = chars
        elif " " in text:
            # 英文：按空格切词
            units = [w for w in text.split(" ") if w]
            if len(units) != len(times):
                return None
        else:
            return None  # 中文但字数对不上 -> 不猜
        try:
            return [Word(text=u, start_ms=int(t[0]), end_ms=int(t[1]))
                    for u, t in zip(units, times, strict=True)]
        except (TypeError, ValueError, IndexError):
            return None

    def _to_segments(self, res, options: JobOptions, total_ms: int) -> list[Segment]:
        if not res:
            return []
        r0 = res[0] if isinstance(res, list) else res
        max_chars = options.max_chars

        # 1) 句级时间戳（最佳）：funasr sentence_info -> [{text,start,end,spk?}]
        #    funasr 会在逗号处也断；max_chars<=0 时合并到句末标点，与 onnx 默认一致。
        sent = r0.get("sentence_info") if isinstance(r0, dict) else None
        if sent:
            raw = []
            for s in sent:
                text = _SPECIAL.sub("", str(s.get("text", ""))).strip()
                if text:
                    # `or 0` 兼容 start/end 缺失或为 None（int(None) 会抛 TypeError）
                    raw.append([int(s.get("start") or 0), int(s.get("end") or 0), text,
                                s.get("spk"), s.get("timestamp")])
            if raw:
                raw = self._recover_dropped_tail(raw, r0, total_ms)
                if not (max_chars and max_chars > 0):
                    raw = _merge_to_terminal(raw)
                # 上游不管行长（那是字幕需求），所以每句还要按「每行最多几个字」再拆一刀；
                # 拆出来的每一行仍用这句的真实起止时间，按字数比例分配到句内。
                segs: list[Segment] = []
                for st, en, tx, spk, times in raw:
                    words = self._words_from_times(tx, times)
                    for piece in segment_span(tx, st, en, max_chars=max_chars):
                        piece.speaker = f"spk{spk}" if spk is not None else None
                        if words:
                            attach_words([piece], [(w.text, w.start_ms) for w in words])
                        segs.append(piece)
                return segs
        # 2) 逐字时间戳：上游 output_timestamp=True 给的 words + timestamp。
        #    SenseVoice 走这条（上游没有 timestamp 头，但能按 60ms/帧反推），
        #    时间是真的，不用再按字数比例摊。
        pieces = self._per_token_pieces(r0)
        if pieces:
            return attach_words(
                segment_timed(pieces, total_ms, max_chars=max_chars), pieces)

        # 3) 词/字级时间戳：timestamp=[[s,e],...] 对齐 text -> 复用 segment_timed
        text = _SPECIAL.sub("", str(r0.get("text", ""))).strip() if isinstance(r0, dict) else ""
        ts = r0.get("timestamp") if isinstance(r0, dict) else None
        if text and ts and len(ts) == len(text.replace(" ", "")):
            pieces = []
            i = 0
            for ch in text:
                if ch == " ":
                    continue
                start = int(ts[i][0])
                pieces.append((ch, start))
                i += 1
            return segment_timed(pieces, total_ms, max_chars=max_chars)

        # 4) 兜底：只有整段文本 -> 按标点切句 + 比例分配整段时长
        if text:
            return segment_span(text, 0, total_ms, max_chars=max_chars)
        return []
