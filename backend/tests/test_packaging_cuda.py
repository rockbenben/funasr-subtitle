"""打包前必须挡住旧 torch / CPU wheel / 混装；构建机不必有显卡。"""
import runpy
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

VERIFY = Path(__file__).resolve().parents[2] / "packaging/verify-cuda.py"


@pytest.mark.parametrize(("torch_version", "audio_version", "cuda", "error"), [
    ("2.8.0+cu128", "2.8.0+cu128", "12.8", None),
    ("2.5.1+cu121", "2.5.1+cu121", "12.1", "torch: expected"),
    ("2.8.0+cpu", "2.8.0+cpu", None, "torch: expected"),
    ("2.8.0+cu128", "2.7.1+cu128", "12.8", "torchaudio: expected"),
    ("2.8.0+cu128", "2.8.0+cu128", None, "expected CUDA"),
])
def test_build_cuda_stack_gate(monkeypatch, torch_version, audio_version, cuda, error):
    # Deliberately no torch.cuda attribute: this check must also work on GPU-less CI.
    monkeypatch.setitem(sys.modules, "torch", SimpleNamespace(
        __version__=torch_version, version=SimpleNamespace(cuda=cuda),
    ))
    monkeypatch.setitem(sys.modules, "torchaudio", SimpleNamespace(__version__=audio_version))
    verify = runpy.run_path(str(VERIFY))["verify"]
    if error:
        with pytest.raises(RuntimeError, match=error):
            verify()
    else:
        verify()


def test_missing_audio_dll_stops_packaging(monkeypatch):
    import importlib

    def broken_import(name):
        raise OSError("Could not load torchaudio DLL")

    monkeypatch.setattr(importlib, "import_module", broken_import)
    with pytest.raises(SystemExit) as exc:
        runpy.run_path(str(VERIFY), run_name="__main__")
    assert exc.value.code == 1
