"""用小文件实际分卷、合并，防止 CUDA 包变大后漏掉第三卷。"""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SHELL = shutil.which("powershell")
pytestmark = pytest.mark.skipif(os.name != "nt" or not SHELL, reason="Windows packaging only")


@pytest.mark.parametrize("size", [2, 3, 4])
def test_every_volume_is_merged(tmp_path, size):
    archive = tmp_path / "portable.zip"
    original = bytes(range(size * 3))
    archive.write_bytes(original)
    result = subprocess.run(
        [SHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
         str(ROOT / "packaging/split-archive.ps1"), "-Zip", str(archive), "-PartSize", "3"],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert not archive.exists()
    parts = sorted(tmp_path.glob("*.zip.*"))
    assert len(parts) == size
    assert b"".join(part.read_bytes() for part in parts) == original
    result = subprocess.run(
        [os.environ["COMSPEC"], "/c", str(tmp_path / "portable-merge.bat")],
        cwd=tmp_path, stdin=subprocess.DEVNULL, capture_output=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert archive.read_bytes() == original
    assert not list(tmp_path.glob("*.zip.*"))


def test_small_archive_needs_no_volumes(tmp_path):
    archive = tmp_path / "portable.zip"
    archive.write_bytes(b"abc")
    result = subprocess.run(
        [SHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
         str(ROOT / "packaging/split-archive.ps1"), "-Zip", str(archive), "-PartSize", "3"],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert archive.read_bytes() == b"abc"
    assert not list(tmp_path.glob("*.bat"))
