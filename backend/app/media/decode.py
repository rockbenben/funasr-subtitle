"""ffmpeg 封装：任意 mp4/mkv/mp3/m4a/wav... -> 16kHz 单声道 float32 PCM（§11）。

视频只取音轨。解码前先用 ffprobe 校验：不是受支持的媒体 / 无音轨 -> 立即报错，
不进入长时间解码。解码中按时长实时上报进度，并有看门狗，避免「卡在解码」。

PCM 用 `_PcmSink` 流式读进按 ffprobe 时长预分配的缓冲区：旧的「攒 list[bytes] 再
b"".join()"」会让峰值内存变成音频数据的 2 倍（2 小时视频 916MB → 现 475MB），
详见 engine/README.md §7。
"""
from __future__ import annotations

import contextlib
import json
import subprocess
import threading
import time
from collections.abc import Callable
from pathlib import Path

import numpy as np

from ..config import STALL_TIMEOUT_S, ffmpeg_path

TARGET_SR = 16_000

# 看门狗：解码期间若连续这么久没有任何进度/输出，判定卡死并杀掉 ffmpeg。
# 可用环境变量 FUNASR_SUBTITLE_STALL_TIMEOUT 调整（见 config）。
_STALL_TIMEOUT_S = STALL_TIMEOUT_S
# Windows 下隐藏子进程控制台窗口（windowed 打包时避免黑框闪现）。
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class DecodeError(RuntimeError):
    """解码/格式相关错误；message 为面向用户的中文说明，detail 放原始技术输出（界面折叠显示）。"""

    def __init__(self, message: str, detail: str | None = None) -> None:
        super().__init__(message)
        self.detail = detail


class DecodeCancelled(DecodeError):
    """解码被用户取消（与失败区分，便于上层报「已取消」而非「失败」）。"""


_READ_CHUNK = 1 << 20  # 1MB：每次从 ffmpeg stdout 取的数据块


def _estimate_pcm_bytes(total_ms: int | None) -> int:
    """按 ffprobe 时长预估 f32le@16k 的字节数（+1s 余量）。"""
    if not total_ms or total_ms <= 0:
        return 0
    samples = int(total_ms * TARGET_SR / 1000) + TARGET_SR
    return samples * 4


class _PcmSink:
    """把 ffmpeg stdout 的 f32le PCM **流式**读进一块可增长的 numpy 缓冲区。

    为什么不用 `list[bytes]` + `b"".join()`：那会让峰值内存变成音频数据的 2 倍
    （2 小时视频 ≈ 460MB -> 峰值 ~920MB），长视频很容易把内存顶爆。
    这里直接读进目标缓冲区，常驻只有 1 份；只有 ffprobe 时长估短时才按倍数扩容，
    扩容才是唯一的一次性拷贝代价。
    """

    _MIN_BYTES = _READ_CHUNK  # 时长未知时的起容量

    def __init__(self, capacity_bytes: int = 0) -> None:
        self._buf = np.empty(max(capacity_bytes, self._MIN_BYTES), dtype=np.uint8)
        self._mv = memoryview(self._buf)
        self._filled = 0

    def read_from(self, stream, chunk: int = _READ_CHUNK) -> int:
        """从二进制流读一块进缓冲区；返回读到的字节数（0 表示 EOF）。

        与 `stream.read(chunk)` 语义一致：阻塞到填满本次窗口或 EOF。
        窗口恒定上限 `chunk`（不让 readinto 一次吞掉整个缓冲区），
        这样主线程能按块刷新活动时间、看门狗才能识别 ffmpeg 卡死。
        """
        space = min(chunk, len(self._buf) - self._filled)
        if space <= 0:  # 缓冲区已满才扩容（读不到东西时绝不扩容，避免 EOF 前无谓翻倍）
            self._grow(chunk)
            space = min(chunk, len(self._buf) - self._filled)
        n = stream.readinto(self._mv[self._filled:self._filled + space])
        if not n:
            return 0
        self._filled += n
        return n

    def _grow(self, need: int) -> None:
        new = np.empty(max(len(self._buf) * 2, len(self._buf) + need), dtype=np.uint8)
        new[: self._filled] = self._buf[: self._filled]
        self._mv.release()
        self._buf = new
        self._mv = memoryview(new)

    def to_audio(self) -> np.ndarray:
        """导出 float32 单声道数组。

        f32le 每样本 4 字节：被杀/截断时尾部可能残留不足 4 字节，截到 4 的整数倍，
        否则 np.frombuffer/view 会抛错（非 DecodeError）。
        """
        usable = (self._filled // 4) * 4
        return self._buf[:usable].view("<f4").astype(np.float32, copy=False)


def _ffprobe_path() -> Path:
    # ffprobe 与 ffmpeg 同目录；开发机回退到 PATH
    ff = ffmpeg_path()
    if ff.name.lower() == "ffmpeg":
        return Path("ffprobe")  # ffmpeg 是 PATH 裸名 -> ffprobe 也假定在 PATH
    cand = ff.with_name("ffprobe.exe")  # 同目录的 ffprobe.exe（便携包/打包）
    return cand if cand.exists() else Path("ffprobe")


def probe_media(path: str | Path) -> dict:
    """ffprobe 校验文件并返回 {duration_ms, has_audio}。

    不是受支持的媒体格式 / 文件损坏 / 无音轨 -> 抛 DecodeError（中文，面向用户）。
    这是「提前报错」的关口：在真正解码前几百毫秒内完成。
    """
    src = Path(path)
    if not src.exists():
        raise DecodeError("找不到这个文件，可能已经被删掉了。重新拖一次试试。", detail=str(src))
    if src.stat().st_size == 0:
        raise DecodeError("文件是空的（0 字节）")

    probe = _ffprobe_path()
    try:
        out = subprocess.run(
            [
                str(probe), "-v", "error", "-print_format", "json",
                "-show_format", "-show_streams", str(src),
            ],
            capture_output=True, timeout=30, creationflags=_NO_WINDOW,
            # ffprobe 输出 UTF-8 JSON；必须按 UTF-8 解码，不能用系统 locale。
            # 中文 Windows 默认 cp936(GBK)，遇到非 ASCII 元数据(标题/注释)会 UnicodeDecodeError，
            # 致 stdout 变空 -> 误判「无音频轨道」。errors=replace 保证坏字节不致命。
            encoding="utf-8", errors="replace",
        )
    except FileNotFoundError as e:  # ffprobe 不存在
        raise DecodeError("找不到 ffprobe（应与 ffmpeg 同目录）") from e
    except subprocess.TimeoutExpired as e:
        raise DecodeError("探测文件超时：文件可能已损坏，或不是受支持的媒体格式") from e

    if out.returncode != 0:
        detail = (out.stderr or "").strip().splitlines()
        tail = detail[-1] if detail else ""
        raise DecodeError(
            "读不出来：这个文件可能不是音视频，或者已经损坏。",
            detail=tail or None,
        )

    try:
        info = json.loads(out.stdout or "{}")
    except json.JSONDecodeError as e:
        raise DecodeError("无法解析文件信息（ffprobe 输出异常）") from e

    streams = info.get("streams", []) or []
    has_audio = any(s.get("codec_type") == "audio" for s in streams)
    if not has_audio:
        has_video = any(s.get("codec_type") == "video" for s in streams)
        if has_video:
            raise DecodeError("这个视频里没有音频轨道，无法转写字幕")
        raise DecodeError("文件里没有音频轨道，无法转写字幕")

    # 时长仅用于进度估算，不致命。某些容器/流式文件 ffprobe 报 "N/A" 或非数值，
    # 不能让一个**有音频的正常文件**因 float("N/A") 抛异常而整体失败。
    dur = info.get("format", {}).get("duration")
    try:
        total_ms = int(float(dur) * 1000) if dur else None
    except (ValueError, TypeError):
        total_ms = None
    return {"duration_ms": total_ms, "has_audio": True}


def probe_duration_ms(path: str | Path) -> int | None:
    """探测媒体时长（毫秒）。失败返回 None（仅用于进度估算，不致命）。"""
    try:
        return probe_media(path).get("duration_ms")
    except DecodeError:
        return None


def _parse_progress_us(line: str) -> int | None:
    """从 ffmpeg -progress 行解析 out_time_us（已处理的音频时间，微秒）。

    只认 out_time_us（现代 ffmpeg 必有），不碰 out_time_ms：后者历史上单位时而是
    微秒时而是毫秒，乘 1000 在某些构建上会把进度顶到 99% 卡住。
    """
    line = line.strip()
    if line.startswith("out_time_us="):
        val = line.split("=", 1)[1].strip()
        if val.isdigit():
            return int(val)
    return None


def decode_to_16k_mono(
    path: str | Path,
    progress: Callable[[float], None] | None = None,
    cancel: threading.Event | None = None,
) -> tuple[np.ndarray, int]:
    """解码为 (float32 单声道 -1..1, 16000)。

    流程：ffprobe 提前校验 -> ffmpeg 输出原始 f32le PCM 到 stdout（不落临时 wav）。
    进度：边读 stderr 的 -progress 边按时长估算 0..100；看门狗防卡死。
    cancel：传入后，置位即杀掉 ffmpeg 并抛 DecodeCancelled（取消能即时打断解码）。
    """
    src = Path(path)
    # 关口：不支持的格式 / 无音轨在这里就报错，不进入长时间解码。
    meta = probe_media(src)
    total_ms = meta["duration_ms"]

    ff = ffmpeg_path()
    cmd = [
        str(ff), "-nostdin", "-hide_banner", "-loglevel", "error",
        "-i", str(src),
        "-vn",                      # 丢弃视频
        "-ac", "1",                 # 单声道
        "-ar", str(TARGET_SR),      # 16k
        "-f", "f32le",              # 原始 float32 little-endian
        "-progress", "pipe:2",      # 进度写 stderr
        "pipe:1",                   # PCM 写 stdout
    ]
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        creationflags=_NO_WINDOW,
    )

    if progress:
        progress(0.0)

    # 活动时间戳：stdout 收到数据或 stderr 有进度行都算「活着」；看门狗据此判卡死。
    last_active = [time.monotonic()]
    err_lines: list[str] = []          # 仅保留末尾若干行，避免坏文件刷爆内存
    _ERR_CAP = 50
    killed = {"stall": False, "cancel": False}  # 看门狗的杀进程原因（避免事后靠时间戳反推）

    def _pump_stderr() -> None:
        assert proc.stderr is not None
        for raw in proc.stderr:
            last_active[0] = time.monotonic()
            line = raw.decode("utf-8", "ignore")
            us = _parse_progress_us(line)
            if us is not None and total_ms and progress:
                pct = max(0.0, min(99.0, us / 1000.0 / total_ms * 100.0))
                progress(pct)
            elif not line.startswith(("out_time", "total_size", "bitrate", "speed",
                                      "frame=", "fps=", "stream_", "progress=",
                                      "dup_frames", "drop_frames")):
                s = line.strip()
                if s:
                    err_lines.append(s)
                    if len(err_lines) > _ERR_CAP:
                        del err_lines[0]

    def _watchdog() -> None:
        while proc.poll() is None:
            if cancel is not None and cancel.is_set():
                killed["cancel"] = True
                proc.kill()
                break
            if time.monotonic() - last_active[0] > _STALL_TIMEOUT_S:
                killed["stall"] = True
                proc.kill()
                break
            time.sleep(0.5)

    t_err = threading.Thread(target=_pump_stderr, daemon=True)
    t_dog = threading.Thread(target=_watchdog, daemon=True)
    t_err.start()
    t_dog.start()

    try:
        # 主线程持续把 stdout（PCM）读进预分配缓冲区，收到数据即刷新活动时间。
        sink = _PcmSink(capacity_bytes=_estimate_pcm_bytes(total_ms))
        assert proc.stdout is not None
        while True:
            if not sink.read_from(proc.stdout):
                break
            last_active[0] = time.monotonic()

        code = proc.wait()
    finally:
        # 任何路径都要回收：杀进程、收线程、关管道，避免孤儿 ffmpeg / FD 泄漏。
        if proc.poll() is None:
            proc.kill()
            with contextlib.suppress(Exception):
                proc.wait(timeout=5)
        t_err.join(timeout=2.0)
        t_dog.join(timeout=2.0)
        for stream in (proc.stdout, proc.stderr):
            with contextlib.suppress(Exception):
                if stream is not None:
                    stream.close()

    if killed["cancel"] or (cancel is not None and cancel.is_set()):
        raise DecodeCancelled("已取消")
    if code != 0:
        if killed["stall"]:
            raise DecodeError("解码卡住已超时中断：文件可能损坏或编码异常")
        msg = " ".join(err_lines).strip()
        raise DecodeError(
            "解码失败，可以点「查看详情」看后台输出。",
            detail=f"ffmpeg code {code}" + (f": {msg[:400]}" if msg else ""),
        )

    # f32le：每样本 4 字节。被杀/截断时尾部可能残留不足 4 字节，to_audio 已截到整数倍。
    audio = sink.to_audio()
    if audio.size == 0:
        raise DecodeError("解码出来的音频是空的（可能没有有效音轨）")
    if progress:
        progress(100.0)
    return audio, TARGET_SR
