"""引擎集成测试（需已下载模型）。

仅在默认模型已在缓存时运行；否则跳过（CI 上无模型则不强制下载）。
"""
from pathlib import Path

import pytest

from app.models import DEFAULT_MODEL_ID, ModelManager

FIXTURE = Path(__file__).parent / "fixtures" / "sample_zh.wav"
EN_FIXTURE = Path(__file__).parent / "fixtures" / "sample_en_runon.wav"

_mgr = ModelManager()
_have_models = _mgr.is_downloaded(DEFAULT_MODEL_ID)

pytestmark = pytest.mark.skipif(
    not (_have_models and FIXTURE.exists()),
    reason="默认模型未下载或缺测试音频，跳过集成测试",
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
