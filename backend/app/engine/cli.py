"""命令行入口：喂一个音频/视频，打印 segments / 导出字幕。

用法：
    python -m app.engine.cli INPUT [--lang auto] [--model <id>] [--format srt|vtt|txt|json] [-o out]
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from ..media import decode_to_16k_mono
from ..schemas import JobOptions
from ..subtitle import export_segments
from . import build_engine


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="funasr-subtitle-cli", description="funasr-subtitle 本地字幕生成 CLI")
    p.add_argument("input", help="音频/视频文件路径")
    p.add_argument("--lang", default="auto",
                   choices=["auto", "zh", "yue", "en", "ja", "ko"])
    p.add_argument("--model", default="", help="ASR 模型 id（默认 SenseVoiceSmall）")
    p.add_argument("--format", default="srt",
                   choices=["srt", "vtt", "txt", "json"])
    p.add_argument("-o", "--output", default="", help="输出文件（默认打印到 stdout）")
    p.add_argument("--gpu", action="store_true", help="尝试 DirectML 提速（M8，可能未启用）")
    args = p.parse_args(argv)

    def progress(stage, pct):  # noqa: ANN001
        print(f"  [{stage:>10}] {pct:5.1f}%", file=sys.stderr, flush=True)

    t0 = time.perf_counter()
    print(f"decoding {args.input} ...", file=sys.stderr, flush=True)
    wav, sr = decode_to_16k_mono(args.input, progress=lambda pct: progress("decoding", pct))

    print("loading engine (first run downloads models) ...", file=sys.stderr, flush=True)
    engine = build_engine(args.model, use_gpu=args.gpu)
    engine.load()

    opts = JobOptions(model_id=args.model, language=args.lang)
    segments = engine.transcribe(wav, sr, opts, progress=progress)
    dt = time.perf_counter() - t0

    audio_sec = len(wav) / sr
    print(f"\n{len(segments)} segments | audio {audio_sec:.1f}s | wall {dt:.1f}s "
          f"| RTF {dt / max(audio_sec, 0.01):.3f}", file=sys.stderr, flush=True)

    out = export_segments(segments, args.format)
    if args.output:
        # 用 write_bytes 保持 LF：write_text 在 Windows 会把 \n 转成 \r\n，
        # 与 API 导出(内存字符串, LF)不一致。统一 LF。
        Path(args.output).write_bytes(out.encode("utf-8"))
        print(f"wrote {args.output}", file=sys.stderr)
    else:
        print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
