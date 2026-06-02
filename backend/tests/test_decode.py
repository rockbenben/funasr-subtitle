"""解码层：不支持的格式 / 空文件 / 无音轨应提前报 DecodeError，而不是卡死。"""
import shutil
import subprocess
import time

import pytest

from app.media import DecodeError, decode_to_16k_mono, probe_media

_HAVE_FFMPEG = bool(shutil.which("ffmpeg")) and bool(shutil.which("ffprobe"))


def test_bogus_file_fast_fails(tmp_path):
    p = tmp_path / "bogus.mp4"
    p.write_bytes(b"definitely not a media file" * 50)
    t0 = time.monotonic()
    with pytest.raises(DecodeError):
        decode_to_16k_mono(str(p))
    assert time.monotonic() - t0 < 10  # 提前报错，不是长时间卡解码


def test_empty_file_reports_clearly(tmp_path):
    p = tmp_path / "empty.wav"
    p.write_bytes(b"")
    with pytest.raises(DecodeError):
        probe_media(str(p))


def test_missing_file(tmp_path):
    with pytest.raises(DecodeError):
        probe_media(str(tmp_path / "nope.mp4"))


@pytest.mark.skipif(not _HAVE_FFMPEG, reason="需要 ffmpeg/ffprobe")
def test_probe_mkv_with_nonascii_metadata(tmp_path):
    """回归：含非 ASCII 元数据的 MKV。

    ffprobe 输出 UTF-8 JSON；若 probe 用系统 locale(中文 Windows=GBK)解码，UTF-8 的
    元数据(标题/注释)会触发 UnicodeDecodeError，stdout 变空 -> 误判「文件里没有音频轨道」。
    必须按 UTF-8 解码。
    """
    src = tmp_path / "vid_中文.mkv"
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
         "-metadata", "title=中文标题测试",
         "-metadata", "comment=你好世界，这是一段说明文字。",
         "-c:a", "aac", str(src)],
        check=True,
    )
    info = probe_media(str(src))
    assert info["has_audio"] is True
