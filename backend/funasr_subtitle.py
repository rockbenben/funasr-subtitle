"""funasr-subtitle.exe 入口（PyInstaller windowed）。

绑定空闲端口 -> 起 FastAPI -> 开默认浏览器 -> 托盘（§4）。

windowed 模式下没有控制台，未捕获异常会丢失。这里把启动期 stderr 与异常
落到 %LOCALAPPDATA%\\funasr-subtitle\\startup.log，便于排障。
"""
import multiprocessing
import os
import sys
import traceback
from pathlib import Path


def _log_path() -> Path:
    base = os.environ.get("FUNASR_SUBTITLE_DATA_DIR") or os.path.join(
        os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "funasr-subtitle")
    p = Path(base)
    p.mkdir(parents=True, exist_ok=True)
    return p / "startup.log"


def _main() -> None:
    log = _log_path()
    # windowed 下 sys.stderr 可能为 None；接管到日志文件
    try:
        fh = open(log, "a", encoding="utf-8", buffering=1)
        if sys.stderr is None:
            sys.stderr = fh
        if sys.stdout is None:
            sys.stdout = fh
        fh.write("=== funasr-subtitle starting ===\n")
    except Exception:
        fh = None

    try:
        from app.main import run
        run()
    except Exception:
        tb = traceback.format_exc()
        try:
            with open(log, "a", encoding="utf-8") as f:
                f.write(tb + "\n")
        except Exception:
            pass
        raise


if __name__ == "__main__":
    multiprocessing.freeze_support()  # PyInstaller 下子进程安全
    _main()
