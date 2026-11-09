"""在不冻结应用的情况下验证便携包的解码工具闸门。"""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SHELL = shutil.which("powershell")
pytestmark = pytest.mark.skipif(os.name != "nt" or not SHELL, reason="Windows packaging only")


def _verify(tools, sample=None):
    return subprocess.run(
        [SHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
         str(ROOT / "packaging/verify-media.ps1"), "-ToolDir", str(tools),
         "-Sample", str(sample or ROOT / "backend/tests/fixtures/sample_zh.wav")],
        env={**os.environ, "PATH": ""}, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=30,
    )


def test_missing_bundled_tool_fails_even_without_path(tmp_path):
    result = _verify(tmp_path)
    assert result.returncode != 0
    assert "Missing bundled tool" in result.stderr


@pytest.fixture()
def bundled_tools(tmp_path):
    for name in ("ffmpeg", "ffprobe"):
        source = shutil.which(name)
        if not source:
            pytest.skip(f"missing {name}")
        shutil.copy2(Path(source).resolve(), tmp_path / f"{name}.exe")
    return tmp_path


def test_bundled_tools_probe_and_decode_without_path(bundled_tools):
    result = _verify(bundled_tools)
    assert result.returncode == 0, result.stderr


def test_invalid_media_stops_packaging(bundled_tools):
    sample = bundled_tools / "broken.wav"
    sample.write_bytes(b"not audio")
    result = _verify(bundled_tools, sample)
    assert result.returncode != 0
