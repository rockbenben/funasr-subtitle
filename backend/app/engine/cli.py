"""命令行入口：喂一个音频/视频，打印 segments / 导出字幕。

用法：
    python -m app.engine.cli INPUT [--lang auto] [--model <id>] [--format srt|vtt|txt|json] [-o out]

切句/文本相关选项与界面上的同名设置一一对应（默认即界面默认值）：
    --max-chars N     每行最多几个字（显示宽度：中日韩=1，拉丁/数字=0.5），0=不限制
    --pause-ms N      说话停顿超过 N 毫秒就断句（上游 VAD 的静音阈值），0=不按停顿断
    --itn/--no-itn    数字规范化：把「二零二五年」写成「2025年」（--no-itn 保留中文数字）
    --punctuation M   标点模式：auto（默认）/ on（都加）/ off（都不加句末标点）
    --hotwords "..."  热词，空格分隔
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


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="funasr-subtitle-cli",
        description="funasr-subtitle 本地字幕生成 CLI",
    )
    p.add_argument("input", help="音频/视频文件路径")
    p.add_argument("--lang", default="auto",
                   choices=["auto", "zh", "yue", "en", "ja", "ko"])
    p.add_argument("--model", default="", help="ASR 模型 id（默认 SenseVoiceSmall）")
    p.add_argument("--format", default="srt",
                   choices=["srt", "vtt", "txt", "json"])
    p.add_argument("-o", "--output", default="", help="输出文件（默认打印到 stdout）")
    p.add_argument("--gpu", action="store_true", help="尝试 DirectML 提速（M8，可能未启用）")
    p.add_argument("--max-chars", type=int, default=30,
                   help="每行最多几个字（0=不限制，默认 30）")
    p.add_argument("--pause-ms", type=int, default=0,
                   help="说话停顿超过多少毫秒就断句（0=不按停顿断，默认 0）")
    p.add_argument("--itn", dest="itn", action="store_true", default=True,
                   help="数字规范化：中文数字写成阿拉伯数字（默认开）")
    p.add_argument("--no-itn", dest="itn", action="store_false",
                   help="不规范化数字，保留「二〇二五年」原样")
    p.add_argument("--punctuation", default="auto", choices=["auto", "on", "off"],
                   help="标点模式：auto=中文加/英文按停顿（默认），on=都加，off=都不加句末标点")
    p.add_argument("--hotwords", default="", help="热词，空格分隔（可空）")
    return p


def _options_from_args(args: argparse.Namespace) -> JobOptions:
    """命令行参数 -> JobOptions（与界面上的设置同一个模型）。"""
    return JobOptions(
        model_id=args.model,
        language=args.lang,
        max_chars=max(0, min(100, args.max_chars)),
        pause_ms=max(0, min(5000, args.pause_ms)),
        itn=args.itn,
        punctuation=args.punctuation,
        hotwords=args.hotwords,
    )


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    def progress(stage, pct):  # noqa: ANN001
        print(f"  [{stage:>10}] {pct:5.1f}%", file=sys.stderr, flush=True)

    t0 = time.perf_counter()
    print(f"decoding {args.input} ...", file=sys.stderr, flush=True)
    wav, sr = decode_to_16k_mono(args.input, progress=lambda pct: progress("decoding", pct))

    print("loading engine (first run downloads models) ...", file=sys.stderr, flush=True)
    engine = build_engine(args.model, use_gpu=args.gpu)
    engine.load()

    segments = engine.transcribe(wav, sr, _options_from_args(args), progress=progress)
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
