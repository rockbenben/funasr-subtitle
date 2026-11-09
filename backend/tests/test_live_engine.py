"""流式接口与缓存合同；有本地模型时额外跑上游真实推理，不在测试里下载。"""
import importlib.util
from pathlib import Path

import numpy as np
import pytest

from app.engine.live import LiveEngine
from app.models import ModelManager
from app.models.registry import STREAMING_PARAFORMER


@pytest.mark.parametrize("full", [False, True])
def test_upstream_cache_and_final_are_forwarded(full):
    engine = LiveEngine()
    engine.full = full
    calls = []

    class Model:
        def __call__(self, wav, param_dict):
            calls.append(param_dict)
            return [{"preds": ("中文", ["中", "文"])}]

        def generate(self, **kwargs):
            calls.append(kwargs)
            return [{"text": "中文"}]

    engine.model = Model()
    assert engine.feed(np.zeros(9600, dtype=np.float32)) == "中文"
    assert engine.feed(np.zeros(0, dtype=np.float32), final=True) == "中文"
    assert calls[0]["cache"] is calls[1]["cache"] is engine.cache
    assert calls[0]["is_final"] is False and calls[1]["is_final"] is True
    assert LiveEngine().cache is not engine.cache


def test_streaming_onnx_needs_decoder_and_frontend_files(tmp_path):
    manager = ModelManager(root=tmp_path)
    spec = STREAMING_PARAFORMER
    model = tmp_path / spec.repo
    model.mkdir(parents=True)
    (model / "model_quant.onnx").touch()
    assert not manager.is_downloaded(spec.id)
    for filename in spec.required_files:
        (model / filename).touch()
    assert manager.is_downloaded(spec.id)
    assert manager._full_parts(spec) == [(spec.full_repo, spec.full_size_mb)]


@pytest.mark.parametrize("variant", ["onnx", "full"])
def test_native_streaming_recognizes_before_final(variant, monkeypatch):
    stack = "funasr" if variant == "full" else "funasr_onnx"
    if importlib.util.find_spec(stack) is None:
        pytest.skip(f"未安装 {stack}")
    manager = ModelManager()
    spec = STREAMING_PARAFORMER
    ready = manager.full_downloaded(spec.id) if variant == "full" else manager.is_downloaded(spec.id)
    if not ready:
        pytest.skip("本地无流式模型")
    monkeypatch.setenv("FUNASR_SUBTITLE_BACKEND", variant)
    import soundfile as sf

    fixture = Path(__file__).parent / "fixtures" / "sample_zh_numbers.wav"
    wav, sr = sf.read(fixture, dtype="float32")
    assert sr == 16000
    engine = LiveEngine()
    engine.load()
    chunks = [engine.feed(wav[i:i + 9600]) for i in range(0, len(wav), 9600)]
    assert any(chunks), "结束收音前必须已经出字"
    chunks.append(engine.feed(wav[:0], final=True))
    text = "".join(chunks)
    assert "苹果" in text and "电脑" in text
