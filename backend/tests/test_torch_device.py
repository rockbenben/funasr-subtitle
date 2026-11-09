"""CUDA 驱动可见但内核不兼容的回归；CI 无需安装 torch。"""
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pytest

from app.engine import compute_label
from app.engine import torch_device as device_mod
from app.engine.base import EngineOptions
from app.engine.funasr_full_engine import FunasrFullEngine
from app.engine.live import LiveEngine
from app.models.registry import SENSEVOICE
from app.schemas import JobOptions

ERROR = "CUDA error: no kernel image is available for execution on the device"


@pytest.fixture()
def torch(monkeypatch):
    monkeypatch.delenv("FUNASR_SUBTITLE_FORCE_CPU", raising=False)
    monkeypatch.setattr(device_mod, "_cuda_usable", None)
    fake = SimpleNamespace(cuda=SimpleNamespace(is_available=Mock(return_value=True), synchronize=Mock()),
                           ones=Mock(return_value=SimpleNamespace(add_=Mock())))
    monkeypatch.setitem(sys.modules, "torch", fake)
    return fake


def test_visible_but_incompatible_gpu_uses_cpu_and_remembers(torch, monkeypatch):
    torch.ones.side_effect = RuntimeError(ERROR)
    assert device_mod.torch_device() == "cpu"
    assert device_mod.torch_device() == "cpu"
    torch.ones.assert_called_once()
    monkeypatch.setenv("FUNASR_SUBTITLE_BACKEND", "full")
    assert compute_label() == "CPU"


def test_compatible_gpu_probes_and_synchronizes_once(torch):
    assert device_mod.torch_device() == "cuda:0"
    assert device_mod.torch_device() == "cuda:0"
    torch.ones.assert_called_once_with(1, device="cuda:0")
    torch.cuda.synchronize.assert_called_once()


def test_async_kernel_error_is_caught_at_synchronize(torch):
    torch.cuda.synchronize.side_effect = RuntimeError(ERROR)
    assert device_mod.torch_device() == "cpu"


def test_force_cpu_skips_gpu_even_after_success(torch, monkeypatch):
    assert device_mod.torch_device() == "cuda:0"
    monkeypatch.setenv("FUNASR_SUBTITLE_FORCE_CPU", "1")
    assert device_mod.torch_device() == "cpu"
    torch.ones.assert_called_once()


def test_no_gpu_does_not_launch_kernel(torch):
    torch.cuda.is_available.return_value = False
    assert device_mod.torch_device() == "cpu"
    torch.ones.assert_not_called()


def test_other_cuda_errors_are_not_swallowed(torch):
    torch.ones.side_effect = RuntimeError("CUDA out of memory")
    with pytest.raises(RuntimeError, match="out of memory"):
        device_mod.torch_device()


@pytest.mark.parametrize("failure", ["load", "generate"])
def test_full_rebuilds_on_cpu_with_fresh_cache(torch, monkeypatch, tmp_path, failure):
    devices = []

    def build(**kw):
        device = kw["device"]
        devices.append(device)
        if failure == "load" and device == "cuda:0":
            raise RuntimeError(ERROR)

        def generate(**gen):
            if device == "cuda:0":
                gen["cache"]["partial_gpu_state"] = True
                raise RuntimeError(ERROR)
            assert gen["cache"] == {}
            return [{"text": "你好", "words": ["你", "好"], "timestamp": [[0, 40], [50, 90]]}]
        return SimpleNamespace(generate=generate)

    monkeypatch.setitem(sys.modules, "funasr", SimpleNamespace(AutoModel=build))
    monkeypatch.setenv("FUNASR_SUBTITLE_BACKEND", "full")
    monkeypatch.setenv("MODELSCOPE_CACHE", str(tmp_path))
    engine = FunasrFullEngine(EngineOptions(SENSEVOICE.id, str(tmp_path)))
    segs = engine.transcribe(np.zeros(1600), 16000, JobOptions())
    assert "".join(s.text for s in segs) == "你好"
    assert devices == ["cuda:0", "cpu"]
    assert not engine.gpu_active
    assert compute_label() == "CPU"


def test_live_model_initialization_falls_back(torch, monkeypatch, tmp_path):
    devices = []

    def build(**kw):
        devices.append(kw["device"])
        if kw["device"] == "cuda:0":
            raise RuntimeError(ERROR)
        return object()

    monkeypatch.setitem(sys.modules, "funasr", SimpleNamespace(AutoModel=build))
    monkeypatch.setenv("FUNASR_SUBTITLE_BACKEND", "full")
    monkeypatch.setenv("MODELSCOPE_CACHE", str(tmp_path))
    engine = LiveEngine()
    engine.load()
    assert devices == ["cuda:0", "cpu"]


@pytest.mark.parametrize("gpu_error,cpu_error,expected", [
    ("CUDA out of memory", None, ["cuda:0"]),
    (ERROR, "CPU model failed", ["cuda:0", "cpu"]),
])
def test_model_errors_do_not_retry_indefinitely(torch, monkeypatch, tmp_path,
                                               gpu_error, cpu_error, expected):
    devices = []

    def build(**kw):
        devices.append(kw["device"])
        raise RuntimeError(gpu_error if kw["device"] == "cuda:0" else cpu_error)

    monkeypatch.setitem(sys.modules, "funasr", SimpleNamespace(AutoModel=build))
    monkeypatch.setenv("MODELSCOPE_CACHE", str(tmp_path))
    engine = FunasrFullEngine(EngineOptions(SENSEVOICE.id, str(tmp_path)))
    with pytest.raises(RuntimeError, match=cpu_error or gpu_error):
        engine.load()
    assert devices == expected
