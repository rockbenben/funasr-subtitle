"""funasr-onnx 实现的 ASREngine（§10）。

§17 验证结论（详见 engine/README.md）：
- ASR 默认 = iic/SenseVoiceSmall-onnx 的 model_quant.onnx（torch-free）。
  · funasr-onnx 的 SenseVoiceSmall 顶部 `import torch`，但 torch 只在其 __call__ 的
    CTC 贪心解码里用到；我们绕过 __call__，用纯 numpy 解码，运行时无需 torch。
  · SenseVoice 输出**无时间戳**，且文本前带特殊标记 <|lang|><|emo|><|event|><|itn|>，需剥离。
  · 时间戳来源：VAD 把音频切成语音片段，每段 [start_ms,end_ms] 即字幕时间戳；
    每段单独跑 SenseVoice 取文本（句级粒度，足够做字幕）。
- VAD = fsmn-vad onnx（仅含 model_quant.onnx -> quantize=True），输出 [[start_ms,end_ms],...]。
- punc = ct-punc onnx（仅含 model_quant.onnx -> quantize=True），给每段文本补标点。
- torch-stub 必须在 modelscope 完成初始化、且 scipy/librosa 已 torch-free 导入之后再安装。
"""
from __future__ import annotations

import importlib.machinery
import re
import sys
import types
from pathlib import Path

import numpy as np

from ..config import PUNC_CHUNK
from ..models import PUNC_MODEL, VAD_MODEL, ModelManager, get_model
from ..schemas import JobOptions, Segment
from ..subtitle import enforce_monotonic, segment_span, segment_timed, strip_terminal_punct
from ..subtitle.segmentation import _STRIP_TERMINALS
from .base import EngineOptions, ProgressCallback

# 剥离 SenseVoice 的 <|...|> 特殊标记（语言/情感/事件/itn）
_SPECIAL_TOKEN_RE = re.compile(r"<\|[^|>]*\|>")

_funasr_imported = False


def _ensure_funasr():
    """按 §17 的安全顺序导入 funasr_onnx（避免 torch-stub 撞 modelscope/scipy）。"""
    global _funasr_imported
    if _funasr_imported:
        import funasr_onnx
        return funasr_onnx

    # 1) 让 modelscope 在「无 torch」时完成 logger 初始化（它用 find_spec('torch')）
    try:
        import modelscope.hub.snapshot_download  # noqa: F401
    except Exception:  # noqa: BLE001  # 预热导入：失败无所谓，后面真用时再报错
        pass
    # 2) 科学栈在 import 期会探测 torch（scipy array_api_compat），必须先 torch-free 导入
    try:
        import librosa  # noqa: F401
        import scipy.signal  # noqa: F401
    except Exception:  # noqa: BLE001  # 同上：缺包时让后续 import 自己抛出更准确的错
        pass
    # 3) 安装「足够完整」的 torch-stub，挡住 import / duck-typing 探测
    if "torch" not in sys.modules:
        stub = types.ModuleType("torch")
        stub.__spec__ = importlib.machinery.ModuleSpec("torch", loader=None)
        stub.__version__ = "0.0.0-stub-fs"
        stub.Tensor = type("Tensor", (), {})
        sys.modules["torch"] = stub
    # 4) 导入 funasr_onnx
    import funasr_onnx
    _funasr_imported = True
    return funasr_onnx


def directml_available() -> bool:
    """当前 onnxruntime 是否提供 DmlExecutionProvider（全显卡加速，§10/M8）。"""
    try:
        import onnxruntime as ort
        return "DmlExecutionProvider" in ort.get_available_providers()
    except Exception:  # noqa: BLE001  # onnxruntime 缺失/加载失败 -> 探测不到就是没 GPU
        return False


def _patch_directml(funasr_onnx, device_id: int = 0) -> bool:
    """把 funasr-onnx 创建 ONNX session 的 InferenceSession 重定向到 DirectML EP。

    §17.6：funasr-onnx 的 OrtInferSession 只会挂 CUDA/CPU，写死在 utils.utils 里且
    各 bin 以裸名 `InferenceSession` 引用它。这里改 utils.utils.InferenceSession，
    强制 providers=[DML, CPU]，对所有模型（VAD/ASR/punc）统一生效。
    """
    if not directml_available():
        return False
    utils = funasr_onnx.utils.utils
    if getattr(utils, "_fs_dml_patched", False):
        return True
    Orig = utils.InferenceSession
    dml_ep = ("DmlExecutionProvider", {"device_id": int(device_id)})

    def _make(model_file, sess_options=None, providers=None, **kw):
        # DML 需要关闭 mem pattern；arena 在上游已关
        try:
            if sess_options is not None:
                sess_options.enable_mem_pattern = False
        except Exception:  # noqa: BLE001  # 该 EP 不认这个属性就跳过，不影响建 session
            pass
        return Orig(model_file, sess_options=sess_options,
                    providers=[dml_ep, "CPUExecutionProvider"], **kw)

    utils.InferenceSession = _make
    utils._fs_dml_patched = True
    return True


def _clean_text(text: str) -> str:
    return _SPECIAL_TOKEN_RE.sub("", text).strip()


_TERMINAL_CHARS = set("。！？!?….;")  # 含半角 . ; ：normalize 后英文句末用半角
_PUNCT_STRIP = set("。，！？!?…；;：:、,.")  # 英文重标点前先去掉的零星标点
_CJK_RE = re.compile(r"[一-鿿぀-ヿ가-힯]")
# ct-punc 给英文也加的是全角标点，纯拉丁文本转成半角更自然
_FW2HW = {"，": ", ", "。": ". ", "！": "! ", "？": "? ", "；": "; ", "：": ": ", "、": ", "}


def _has_terminal(text: str) -> bool:
    return any(c in _TERMINAL_CHARS for c in text)


def _normalize_latin_punct(text: str) -> str:
    """无中日韩字符（纯英文等）时，把 ct-punc 的全角标点转半角并规整空格。"""
    if _CJK_RE.search(text):
        return text
    for fw, hw in _FW2HW.items():
        text = text.replace(fw, hw)
    return re.sub(r"\s+", " ", text).strip()


def _latin_punct(text: str, bare_text: str, punctuate) -> str:  # noqa: ANN001
    """英文/拉丁标点来源：**原生(SenseVoice)优先**，仅当原生无句末标点才退回 ct-punc。

    §M8 实测：zh-cn ct-punc 对英文会按中文节奏在**错误位置**插句号（"...we build." /
    "data And." 把一句切成几段），而 SenseVoice 自带的英文标点位置正确。故不再无脑用
    ct-punc 覆盖；只有原生完全没有句末标点时，才用 ct-punc 兜底补一份。
    """
    native = _normalize_latin_punct(text)
    if _has_terminal(native):
        return native
    return _normalize_latin_punct(punctuate(bare_text))


def _apply_punct_to_timed(
    timed: list[tuple[str, int]], punct_text: str
) -> list[tuple[str, int]]:
    """把补标点后的文本对齐回带时间戳的 token，保留**真实** CTC 时间。

    原始 token 的字符与 punct_text 的实义字符一一对应；punct_text 里新插入的标点/空格
    用「上一个对齐字符的时间」。这样补标点不会丢掉真实时间戳（不退化成比例分配）。
    """
    ct = [(ch, t) for piece, t in timed for ch in piece]  # 原始逐字 + 时间
    out: list[tuple[str, int]] = []
    p = 0
    last_t = timed[0][1] if timed else 0
    for c in punct_text:
        if c.isspace():
            out.append((c, last_t))
            continue
        while p < len(ct) and ct[p][0].isspace():
            p += 1
        # 大小写不敏感匹配：ct-punc 兜底可能改大小写，若用 == 严格比对，一处不符会让指针
        # 卡住、其后所有 token 时间塌到 last_t。失配时再做有界向前重同步（容忍少量增删字符）。
        if p < len(ct) and ct[p][0].lower() == c.lower():
            last_t = ct[p][1]
            out.append((c, last_t))
            p += 1
        else:
            found = -1
            for k in range(p, min(p + 8, len(ct))):
                if not ct[k][0].isspace() and ct[k][0].lower() == c.lower():
                    found = k
                    break
            if found >= 0:
                last_t = ct[found][1]
                out.append((c, last_t))
                p = found + 1
            else:
                out.append((c, last_t))  # 新插入的标点 -> 用上一个字符的时间
    return out


def _strip_terminal_timed(
    timed: list[tuple[str, int]]
) -> list[tuple[str, int]]:
    """「按停顿」模式：删句末终止标点（保留逗号/小数/缩写内点），保留逐字时间。

    与 subtitle.strip_terminal_punct 同规则，但工作在带时间的逐 token 上。删后文本无句末
    标点，segment_timed 自然只按行宽/时长切，不会再被错位的句号切碎。
    """
    chars = [(ch, t) for piece, t in timed for ch in piece]
    n = len(chars)
    out: list[tuple[str, int]] = []
    for i, (ch, t) in enumerate(chars):
        if ch in _STRIP_TERMINALS:
            continue
        if ch == ".":
            after = chars[i + 1][0] if i + 1 < n else ""
            if after == "" or after.isspace():
                continue
        out.append((ch, t))
    return out


def _ctc_greedy_numpy(ctc_logits: np.ndarray, valid_len: int, blank_id: int = 0) -> list[int]:
    """纯 numpy CTC 贪心解码：argmax -> 去连续重复 -> 去 blank。"""
    x = ctc_logits[:valid_len]
    yseq = x.argmax(axis=-1)
    keep = np.ones(len(yseq), dtype=bool)
    keep[1:] = yseq[1:] != yseq[:-1]
    yseq = yseq[keep]
    yseq = yseq[yseq != blank_id]
    return yseq.tolist()


def _ctc_greedy_with_frames(
    ctc_logits: np.ndarray, valid_len: int, blank_id: int = 0
) -> list[tuple[int, int]]:
    """CTC 贪心解码并保留每个 token 首次出现的帧号 -> [(token_id, frame_idx), ...]。"""
    x = ctc_logits[:valid_len]
    yseq = x.argmax(axis=-1)
    out: list[tuple[int, int]] = []
    prev = blank_id
    for frame, tok in enumerate(yseq):
        tok = int(tok)
        if tok != prev and tok != blank_id:
            out.append((tok, frame))
        prev = tok
    return out


class FunasrEngine:
    """ASREngine 实现。一个实例对应一个已加载的 ASR 模型（+ 共享 VAD/punc）。"""

    SAMPLE_RATE = 16_000

    def __init__(self, opts: EngineOptions, manager: ModelManager | None = None) -> None:
        self.opts = opts
        self.manager = manager or ModelManager(Path(opts.models_root))
        self._vad = None
        self._asr = None
        self._punc = None
        self._loaded = False
        spec = get_model(self.opts.model_id)
        # ASR 自带标点则跳过 ct-punc（§9/§17）
        self._builtin_punc = spec.builtin_punc
        self._engine_kind = spec.engine  # sensevoice | paraformer | contextual
        self.max_chars = 30  # 默认上限（仅 transcribe 未带 options 时的兜底）

    # ---- 加载 ----
    def load(self) -> None:
        if self._loaded:
            return
        funasr_onnx = _ensure_funasr()

        # 确保模型已在本地（缺则同步下载）
        vad_dir = str(self.manager.ensure_dir(VAD_MODEL.id))
        asr_dir = str(self.manager.ensure_dir(self.opts.model_id))

        # GPU：用 DirectML（全显卡，§10/M8）。打补丁把 funasr 的 session 重定向到 DML EP；
        # funasr 自身 device_id 保持 -1（其 CUDA 选择逻辑被我们旁路）。失败则回退 CPU。
        self.gpu_active = bool(self.opts.use_gpu) and _patch_directml(funasr_onnx)
        device_id = "-1"
        threads = self.opts.num_threads or 4

        self._vad = funasr_onnx.Fsmn_vad(
            vad_dir, quantize=True, device_id=device_id, intra_op_num_threads=threads
        )
        # 按引擎类型实例化 ASR
        if self._engine_kind == "paraformer":
            self._asr = funasr_onnx.Paraformer(
                asr_dir, quantize=True, device_id=device_id, intra_op_num_threads=threads
            )
        elif self._engine_kind == "contextual":
            self._asr = funasr_onnx.ContextualParaformer(
                asr_dir, quantize=True, device_id=device_id, intra_op_num_threads=threads
            )
            # funasr-onnx 的 ContextualParaformer.__init__ 漏设 language，
            # __call__ 会读它来选后处理；中文设任意非 "en-bpe" 值即可。
            # 上游修好后这行可以删（见 engine/README.md §6）。
            self._asr.language = "zh"
        else:  # sensevoice
            self._asr = funasr_onnx.SenseVoiceSmall(
                asr_dir, quantize=True, device_id=device_id, intra_op_num_threads=threads
            )
        # 仅当 ASR 不自带标点时才加载 ct-punc（省一次 ~270MB 下载）
        if not self._builtin_punc:
            punc_dir = str(self.manager.ensure_dir(PUNC_MODEL.id))
            self._punc = funasr_onnx.CT_Transformer(
                punc_dir, quantize=True, device_id=device_id, intra_op_num_threads=threads
            )
        self._loaded = True

    # ---- 推理 ----
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
        if sample_rate != self.SAMPLE_RATE:
            raise ValueError(f"expected {self.SAMPLE_RATE} Hz, got {sample_rate}")
        wav = np.asarray(wav, dtype=np.float32)

        def report(stage, pct):  # noqa: ANN001
            if progress:
                progress(stage, float(pct))

        # 1) VAD -> [[start_ms, end_ms], ...]
        report("vad", 0.0)
        vad_out = self._vad(wav)
        spans = vad_out[0] if vad_out else []
        if not spans:
            # 没切出语音：把整段作为一个片段兜底
            spans = [[0, int(len(wav) / self.SAMPLE_RATE * 1000)]]
        report("vad", 100.0)

        # 2) 逐段 ASR -> 标点 -> 按自然语句切分（每段产出多条字幕）
        _valid_langs = {"auto", "zh", "yue", "en", "ja", "ko"}
        lang = options.language if options.language in _valid_langs else "auto"
        max_chars = options.max_chars  # 0=不限制；0 是合法值，不能用 or 回退
        punct_mode = getattr(options, "punctuation", "auto")  # auto|on|off
        segments: list[Segment] = []
        n = len(spans)
        for i, span in enumerate(spans):
            if cancel is not None and cancel.is_set():
                break  # 取消：分段边界即时停止，上层按「已取消」处理
            start_ms, end_ms = int(span[0]), int(span[1])
            a = max(0, int(start_ms / 1000 * self.SAMPLE_RATE))
            b = min(len(wav), int(end_ms / 1000 * self.SAMPLE_RATE))
            chunk = wav[a:b]
            if chunk.size < self.SAMPLE_RATE * 0.05:  # <50ms，跳过
                continue

            if self._engine_kind == "sensevoice":
                # SenseVoice：从 CTC 帧位置推出**真实** token 时间戳，按自然语句切句
                timed = self._sensevoice_timed(chunk, lang, start_ms, end_ms)
                report("asr", (i + 1) / n * 100.0)
                text = "".join(p for p, _ in timed)
                is_cjk = bool(_CJK_RE.search(text))
                # 是否给本段加句末标点：on=都加；off=都不加；auto=中文加、英文按停顿不加。
                add_punct = punct_mode == "on" or (punct_mode == "auto" and is_cjk)
                # 标点策略（始终保留真实 CTC token 时间，避免时间轴错位）：
                # - 加标点·英文：原生 SenseVoice 标点优先（zh ct-punc 会误置英文句号），缺才退 ct-punc。
                # - 加标点·中文：用自带标点；个别无标点的段才补。
                # - 不加（按停顿）：去句末终止标点（保留逗号/小数），只按 VAD 停顿 + 行宽切。
                if timed and add_punct and not is_cjk:
                    bare = [(ch, t) for piece, t in timed for ch in piece
                            if ch not in _PUNCT_STRIP]
                    bare_text = "".join(ch for ch, _ in bare)
                    punct = _latin_punct(text, bare_text, self._punctuate)
                    report("punc", (i + 1) / n * 100.0)
                    timed = _apply_punct_to_timed(bare, punct) if _has_terminal(punct) else bare
                elif timed and add_punct and not _has_terminal(text):
                    punct = self._punctuate(text)
                    report("punc", (i + 1) / n * 100.0)
                    if _has_terminal(punct):
                        timed = _apply_punct_to_timed(timed, punct)
                elif timed and not add_punct:
                    timed = _strip_terminal_timed(timed)
                segments.extend(segment_timed(timed, end_ms, max_chars=max_chars))
            else:
                # Paraformer/Contextual：无逐 token 时间，先标点，再按字数比例切句
                text = self._asr_paraformer(chunk, options.hotwords or "")
                report("asr", (i + 1) / n * 100.0)
                if not text:
                    continue
                is_cjk = bool(_CJK_RE.search(text))
                add_punct = punct_mode == "on" or (punct_mode == "auto" and is_cjk)
                text = self._punctuate(text) if add_punct else strip_terminal_punct(text)
                report("punc", (i + 1) / n * 100.0)
                segments.extend(segment_span(text, start_ms, end_ms, max_chars=max_chars))
        report("punc", 100.0)

        report("assembling", 100.0)
        return enforce_monotonic(segments)

    _PUNC_CHUNK = PUNC_CHUNK  # ct-punc 对超长输入只在结尾加标点；按此长度分块（可配置）

    def _punctuate(self, text: str) -> str:
        try:
            if self._punc is None:  # 懒加载 ct-punc（SenseVoice 默认不预载，遇到无标点文本才下载）
                import funasr_onnx
                punc_dir = str(self.manager.ensure_dir(PUNC_MODEL.id))
                self._punc = funasr_onnx.CT_Transformer(
                    punc_dir, quantize=True, device_id="-1",
                    intra_op_num_threads=self.opts.num_threads or 4)
            if len(text) <= self._PUNC_CHUNK + 30:
                return self._punc_once(text)
            # 长文本分块（在空格处回退，CJK 直接按长度）逐块标点，再拼接
            parts: list[str] = []
            i, n = 0, len(text)
            while i < n:
                end = min(n, i + self._PUNC_CHUNK)
                if end < n:
                    sp = text.rfind(" ", i, end)
                    if sp > i:
                        end = sp
                chunk = text[i:end].strip()
                if chunk:
                    parts.append(self._punc_once(chunk))
                i = end
            return " ".join(parts)
        except Exception:  # noqa: BLE001  # 标点失败/下载失败不致命，保留原文
            return text  # 标点失败/下载失败不致命，保留原文

    def _punc_once(self, text: str) -> str:
        out = self._punc(text)
        return out[0] if isinstance(out, (list, tuple)) else str(out)

    def _asr_paraformer(self, chunk: np.ndarray, hotwords: str = "") -> str:
        res = (self._asr(chunk, hotwords) if self._engine_kind == "contextual"
               else self._asr(chunk))
        if not res:
            return ""
        first = res[0]
        preds = first.get("preds", "") if isinstance(first, dict) else first
        if isinstance(preds, (list, tuple)):  # Paraformer preds 是 (整句, [逐字])
            preds = preds[0] if preds else ""
        return str(preds).strip()

    # SenseVoice 编码器前置 4 个特殊 token（语言/情感/事件/ITN），其后才是语音帧
    _SV_PREFIX_FRAMES = 4

    def _sensevoice_timed(
        self, chunk: np.ndarray, language: str, span_start_ms: int, span_end_ms: int
    ) -> list[tuple[str, int]]:
        """SenseVoice CTC 解码 + 逐 token 时间戳（绝对 ms）。

        §M8 实测：去掉前 4 个前缀帧后，语音帧约 60ms/帧。token 时间按帧位置线性映射到
        [span_start_ms, span_end_ms]，特殊标记 <|...|> 跳过。
        """
        asr = self._asr
        feats, feats_len = asr.extract_feat([chunk])
        lid = np.array([asr._get_lid(language)], dtype=np.int32)
        tn = np.array([asr._get_tnid("withitn")], dtype=np.int32)
        ctc_logits, enc_lens = asr.infer(feats, feats_len, lid, tn)
        ctc_logits = np.asarray(ctc_logits)
        T = int(enc_lens[0])
        pairs = _ctc_greedy_with_frames(ctc_logits[0], T, blank_id=0)  # [(token_id, frame)]

        # 用 sentencepiece 的 piece（带 ▁ 词边界）重建文本，保留英文/混合的空格。
        # 不能用 decode([单 token])：那样会丢掉词间空格（▁ 的去标记是整序列上下文相关的）。
        sp = asr.tokenizer.sp
        prefix = self._SV_PREFIX_FRAMES
        speech_frames = max(1, T - prefix)
        span = max(0, span_end_ms - span_start_ms)
        out: list[tuple[str, int]] = []
        for tok_id, frame in pairs:
            raw = sp.id_to_piece(int(tok_id))
            if _SPECIAL_TOKEN_RE.fullmatch(raw):
                continue  # <|lang|><|emo|><|event|><|itn|> 等特殊标记
            piece = raw.replace("▁", " ")  # ▁ -> 空格（CJK 无 ▁，自然紧贴）
            if not piece:
                continue
            rel = (frame - prefix) / speech_frames
            rel = 0.0 if rel < 0 else (1.0 if rel > 1 else rel)
            out.append((piece, int(span_start_ms + rel * span)))
        return out
