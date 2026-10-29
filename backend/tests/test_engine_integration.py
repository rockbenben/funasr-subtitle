"""引擎集成测试（需已下载模型 + ONNX 推理栈）。

两个前提缺一不可：
- 默认模型已在缓存（否则跳过，CI 上无模型则不强制下载）；
- **funasr_onnx 可导入**——推理栈在 `[onnx]` extra 里，只装了 `[dev]` 的人没有它。
  这里必须一起判，否则「模型已缓存但没装推理栈」会变成一堆 ImportError 而不是干净跳过。
"""
import importlib.util
from pathlib import Path

import pytest

from app.models import DEFAULT_MODEL_ID, ModelManager

FIXTURE = Path(__file__).parent / "fixtures" / "sample_zh.wav"
EN_FIXTURE = Path(__file__).parent / "fixtures" / "sample_en_runon.wav"
# 中文数字样本（Windows TTS 朗读「我有三个苹果，二零二五年要买十五台电脑。」）
NUM_FIXTURE = Path(__file__).parent / "fixtures" / "sample_zh_numbers.wav"

_mgr = ModelManager()
_have_models = _mgr.is_downloaded(DEFAULT_MODEL_ID)
_have_stack = importlib.util.find_spec("funasr_onnx") is not None

pytestmark = pytest.mark.skipif(
    not (_have_stack and _have_models and FIXTURE.exists()),
    reason="缺 funasr_onnx([onnx] extra) / 默认模型未下载 / 缺测试音频，跳过集成测试",
)


def test_transcribe_sample_zh():
    from app.engine import build_engine
    from app.media import decode_to_16k_mono
    from app.schemas import JobOptions

    wav, sr = decode_to_16k_mono(FIXTURE)
    assert sr == 16000 and wav.size > 0

    engine = build_engine()
    engine.load()
    segs = engine.transcribe(wav, sr, JobOptions(language="zh"))

    assert len(segs) >= 1
    joined = "".join(s.text for s in segs)
    # TTS 朗读了「公园」「测试」等词，验证识别大致正确
    assert "公园" in joined or "测试" in joined
    # 时间戳单调且有标点（SenseVoice 自带）
    assert all(s.end_ms >= s.start_ms for s in segs)
    assert any(ch in joined for ch in "，。？")


@pytest.mark.skipif(not EN_FIXTURE.exists(), reason="缺英文测试音频")
def test_transcribe_english_no_midsentence_breaks():
    """英文长段（单个 VAD 语音段）：用 SenseVoice 原生标点切句，不被 zh ct-punc 误切。

    回归点：修复前 zh ct-punc 会在错误位置插句号，切出 "And." / "you." 这类单词成句的
    碎片，并把 4 句切成 7 段。修复后应得到正常句子，无单词碎片。
    """
    from app.engine import build_engine
    from app.media import decode_to_16k_mono
    from app.schemas import JobOptions

    wav, sr = decode_to_16k_mono(EN_FIXTURE)
    engine = build_engine()
    engine.load()
    segs = engine.transcribe(wav, sr, JobOptions(language="en"))

    assert len(segs) >= 1
    joined = " ".join(s.text for s in segs).lower()
    assert "neural" in joined and "recognition" in joined  # 识别大致正确
    # zh ct-punc 误置句号的典型症状：单词成句（去掉句号后是单个纯字母词）。修复后不应出现。
    frags = [
        s.text.strip() for s in segs
        if s.text.strip().endswith(".")
        and len(s.text.strip().split()) == 1
        and s.text.strip().rstrip(".").isalpha()
    ]
    assert not frags, f"出现单词碎片(疑似误置句号): {frags}"
    assert len(segs) <= 5, f"过度切分: {[s.text for s in segs]}"


def _assert_monotonic(segs):
    prev_end = None
    for s in segs:
        assert s.end_ms > s.start_ms, f"非正时长: {s}"
        if prev_end is not None:
            assert s.start_ms >= prev_end, f"cue 重叠/乱序: {s.start_ms} < {prev_end}"
        prev_end = s.end_ms


def _skeleton(text: str) -> str:
    """只留实义字符（去掉标点与空白），用来比较「只换标点、没丢字」。"""
    return "".join(ch for ch in text if ch.isalnum())


def test_timestamps_strictly_monotonic_zh_and_en():
    """真实音频端到端：所有 cue 必须严格递增、不重叠（enforce_monotonic 兜底）。"""
    from app.engine import build_engine
    from app.media import decode_to_16k_mono
    from app.schemas import JobOptions

    engine = build_engine()
    engine.load()
    for fx, lang in [(FIXTURE, "zh"), (EN_FIXTURE, "en")]:
        if not fx.exists():
            continue
        wav, sr = decode_to_16k_mono(fx)
        segs = engine.transcribe(wav, sr, JobOptions(language=lang))
        assert len(segs) >= 1
        _assert_monotonic(segs)


def test_pause_split_breaks_at_pause_on_real_audio():
    """停顿断句（issue #5）：阈值 = 上游 VAD 的静音阈值，说话停下来的地方断成新的一段。

    样本是 TTS 朗读「今天天气非常好，我们一起去公园散步，好不好？这是一个本地语音转写的测试。」
    ——逗号处约 500ms 停顿、句号处约 1s 停顿。阈值 300ms 时应在这些停顿处断成 3 段。
    注意：每段独立转写、独立标点，所以段末的逗号会变成句号（上游行为，已知取舍）。
    """
    from app.engine import build_engine
    from app.media import decode_to_16k_mono
    from app.schemas import JobOptions

    wav, sr = decode_to_16k_mono(FIXTURE)
    engine = build_engine()
    engine.load()
    plain = engine.transcribe(wav, sr, JobOptions(language="zh", max_chars=30))
    paused = engine.transcribe(wav, sr, JobOptions(language="zh", max_chars=30, pause_ms=300))

    assert len(plain) == 2
    assert len(paused) == 3
    # 断点落在停顿处：第一段在逗号处收尾
    assert "今天天气非常好" in paused[0].text
    assert paused[0].text.rstrip().endswith("。")
    # 字没丢（只换了标点）
    assert _skeleton("".join(s.text for s in plain)) == _skeleton("".join(s.text for s in paused))
    _assert_monotonic(paused)


def test_pause_threshold_off_keeps_default_split():
    """阈值 0 = 不按停顿断，行为与从前一致。"""
    from app.engine import build_engine
    from app.media import decode_to_16k_mono
    from app.schemas import JobOptions

    wav, sr = decode_to_16k_mono(FIXTURE)
    engine = build_engine()
    engine.load()
    a = engine.transcribe(wav, sr, JobOptions(language="zh", max_chars=30))
    b = engine.transcribe(wav, sr, JobOptions(language="zh", max_chars=30, pause_ms=0))
    assert [s.text for s in a] == [s.text for s in b]


@pytest.mark.skipif(not NUM_FIXTURE.exists(), reason="缺中文数字测试音频")
def test_itn_switch_writes_chinese_numerals_as_digits():
    """ITN（上游 use_itn / SenseVoice 的 withitn）：口语里的中文数字写成阿拉伯数字。

    音频是 TTS 朗读「我有三个苹果，二零二五年要买十五台电脑。」——
    开着时「二零二五年」→「2025年」、「十五台」→「15台」；关掉则保留中文数字原样。
    「三个苹果」带量词，两种模式都不动（实测）。
    """
    from app.engine import build_engine
    from app.media import decode_to_16k_mono
    from app.schemas import JobOptions

    wav, sr = decode_to_16k_mono(NUM_FIXTURE)
    engine = build_engine()
    engine.load()
    on = engine.transcribe(wav, sr, JobOptions(language="zh", max_chars=30, itn=True))
    off = engine.transcribe(wav, sr, JobOptions(language="zh", max_chars=30, itn=False))
    text_on = "".join(s.text for s in on)
    text_off = "".join(s.text for s in off)

    assert "2025" in text_on, text_on
    assert "15" in text_on, text_on
    assert "2025" not in text_off, text_off
    assert "十五" in text_off, text_off
    # 带量词的不受影响：两种模式都保留「三个」
    assert "三个" in text_on and "三个" in text_off


def test_directml_gpu_if_available():
    """DirectML 可用时，session 应跑在 DmlExecutionProvider 上。"""
    from app.engine import build_engine
    from app.engine.funasr_engine import directml_available
    from app.media import decode_to_16k_mono
    from app.schemas import JobOptions

    if not directml_available():
        pytest.skip("DirectML 不可用")

    wav, sr = decode_to_16k_mono(FIXTURE)
    engine = build_engine(use_gpu=True)
    engine.load()
    assert engine.gpu_active is True
    assert "DmlExecutionProvider" in engine._asr.ort_infer.session.get_providers()
    segs = engine.transcribe(wav, sr, JobOptions(language="zh", use_gpu=True))
    assert len(segs) >= 1
