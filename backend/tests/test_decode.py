"""解码层：不支持的格式 / 空文件 / 无音轨应提前报 DecodeError，而不是卡死。"""
import io
import shutil
import subprocess
import time

import numpy as np
import pytest

from app.media import DecodeError, decode_to_16k_mono, probe_media
from app.media.decode import _estimate_pcm_bytes, _PcmSink

_HAVE_FFMPEG = bool(shutil.which("ffmpeg")) and bool(shutil.which("ffprobe"))


def _feed(data: bytes, chunk: int = 1 << 20, capacity: int = 0) -> _PcmSink:
    sink = _PcmSink(capacity_bytes=capacity)
    stream = io.BytesIO(data)
    while sink.read_from(stream, chunk):
        pass
    return sink


# ---- _PcmSink：流式读 PCM 取代 list[bytes] + b"".join()（消除 2× 峰值内存）----


def test_pcm_sink_matches_join_reference():
    """多种块大小下，输出必须与旧的「攒块再 join」逐样本一致。"""
    rng = np.random.default_rng(1234)
    data = rng.standard_normal(50_000, dtype=np.float32).tobytes()
    want = np.frombuffer(data, dtype="<f4").astype(np.float32, copy=False)
    for chunk in (1 << 20, 64 << 10, 4096, 997):
        got = _feed(data, chunk=chunk).to_audio()
        assert got.dtype == np.float32
        np.testing.assert_array_equal(got, want)


def test_pcm_sink_preallocated_never_regrows():
    """时长估得准时缓冲区**不扩容**：整段音频常驻只占一份，这是省内存的关键。"""
    data = np.linspace(0, 1, 2048, dtype=np.float32).tobytes()  # 8KB
    sink = _PcmSink(capacity_bytes=_estimate_pcm_bytes(2000))
    before = len(sink._buf)
    stream = io.BytesIO(data)
    while sink.read_from(stream):
        pass
    assert len(sink._buf) == before, "预分配足够时不应重新分配"
    assert sink.to_audio().size == 2048


def test_pcm_sink_grows_when_estimate_is_short():
    """时长被低估（ffprobe 报不准）时按倍数扩容，数据仍不能丢。"""
    samples = np.arange(800_000, dtype=np.float32) * 1e-4  # ~3MB > 1MB 起容量
    data = samples.tobytes()
    sink = _feed(data, capacity=0)  # capacity=0 -> 走 _MIN_BYTES，必然触发扩容
    assert len(sink._buf) > 1 << 20, "应已扩容"
    np.testing.assert_array_equal(sink.to_audio(), samples)


def test_pcm_sink_drops_partial_trailing_sample():
    """被杀/截断时尾部残留的不足 4 字节要被丢掉，不能让 np 报非 DecodeError。"""
    sink = _feed(b"\x00\x00\x80\x3f" + b"\x11\x22")  # 1 个完整样本 + 2 字节残片
    audio = sink.to_audio()
    assert audio.size == 1
    assert audio[0] == pytest.approx(1.0)


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


@pytest.mark.skipif(not _HAVE_FFMPEG, reason="需要 ffmpeg/ffprobe")
def test_decode_roundtrip_matches_ffmpeg_reference(tmp_path):
    """回归：流式读取 PCM 的实现必须和 ffmpeg 直接输出**逐样本一致**。

    覆盖 _PcmSink 的预分配路径（时长估得准）与真实 ffmpeg 管道的交互。
    """
    src = tmp_path / "tone.wav"
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=3", str(src)],
        check=True,
    )
    audio, sr = decode_to_16k_mono(str(src))
    assert sr == 16_000
    assert audio.dtype == np.float32
    # 3s ± ffmpeg 尾部余量
    assert 2.5 * 16_000 <= audio.size <= 3.5 * 16_000
    assert np.all(np.isfinite(audio))
    assert np.max(np.abs(audio)) <= 1.0


@pytest.mark.skipif(not _HAVE_FFMPEG, reason="需要 ffmpeg/ffprobe")
def test_decode_no_audio_track_fails_fast(tmp_path):
    """无音轨的视频必须提前报错，不进入解码。"""
    src = tmp_path / "silent.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error",
         "-f", "lavfi", "-i", "color=c=black:duration=1",
         "-c:v", "libx264", str(src)],
        check=True,
    )
    t0 = time.monotonic()
    with pytest.raises(DecodeError):
        decode_to_16k_mono(str(src))
    assert time.monotonic() - t0 < 10
