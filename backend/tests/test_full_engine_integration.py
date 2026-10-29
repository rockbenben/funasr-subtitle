"""完整版（torch 后端）集成测试：需已下载 pt 模型 + funasr/torch 可导入。

CI 只装 `[dev]`（没有 torch/funasr），所以这个文件在 CI 里整个跳过；
本地有 `backend\\.venv-full` 时才会真跑。
"""
import importlib.util
from pathlib import Path

import pytest

FIXTURE = Path(__file__).parent / "fixtures" / "sample_zh.wav"

_have_stack = importlib.util.find_spec("funasr") is not None


def _full_models_ready() -> bool:
    """完整版的「已就位」= pt 权重齐了（见 models/manager.py:full_downloaded）。"""
    if not _have_stack:
        return False
    from app.models import DEFAULT_MODEL_ID, ModelManager

    return ModelManager().full_downloaded(DEFAULT_MODEL_ID)


pytestmark = pytest.mark.skipif(
    not (_have_stack and _full_models_ready() and FIXTURE.exists()),
    reason="缺 funasr(torch 后端) / 完整版模型未下载 / 缺测试音频，跳过完整版集成测试",
)


def _engine_and_wav():
    from app.engine import build_engine
    from app.media import decode_to_16k_mono

    wav, sr = decode_to_16k_mono(FIXTURE)
    engine = build_engine()
    engine.load()
    return engine, wav, sr


def _skeleton(text: str) -> str:
    return "".join(ch for ch in text if ch.isalnum())


def test_full_sensevoice_pause_threshold_splits_at_pause():
    """停顿断句（issue #5）：上游 VAD 的静音阈值 -> 说话停下来的地方断成新的一段。

    完整版 SenseVoice 以前没有字级时间戳，只能把整段文本按字数比例摊时间；
    现在走上游的 sentence_info（VAD 段 = 一句），断点与时间都是真的。
    """
    from app.schemas import JobOptions

    engine, wav, sr = _engine_and_wav()
    plain = engine.transcribe(wav, sr, JobOptions(language="zh", max_chars=30))
    paused = engine.transcribe(wav, sr, JobOptions(language="zh", max_chars=30, pause_ms=300))

    assert len(plain) >= 1
    assert len(paused) == 3, [s.text for s in paused]
    assert "今天天气非常好" in paused[0].text
    # 断点落在停顿处：段末标点被补成句号（上游行为，已知取舍）
    assert paused[0].text.rstrip().endswith("。")
    assert _skeleton("".join(s.text for s in plain)) == _skeleton("".join(s.text for s in paused))


def test_full_path_respects_line_width_limit():
    """完整版以前「每行最多几个字」是摆设（直接用上游的句子，不看行宽）。"""
    from app.schemas import JobOptions
    from app.subtitle.segmentation import _visible_len

    engine, wav, sr = _engine_and_wav()
    segs = engine.transcribe(wav, sr, JobOptions(language="zh", max_chars=10))
    assert segs
    for s in segs:
        # 标点算 0.5 宽，允许略微超出上限（标点跟随前句）
        assert _visible_len(s.text) <= 10 + 1, [s.text for s in segs]
