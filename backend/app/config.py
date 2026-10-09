"""应用路径与运行时配置。

所有持久化数据放在 %LOCALAPPDATA%\\funasr-subtitle\\（§0）：
- models/   模型缓存（首启下载）
- jobs/     每个任务的临时/结果文件，退出时清理
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from platformdirs import user_data_dir

APP_NAME = "funasr-subtitle"
APP_AUTHOR = False  # platformdirs: 不加作者层级 -> %LOCALAPPDATA%\funasr-subtitle


# ---- 可调开关（全部可用环境变量覆盖，方便用户自行调节）----
# 约定：环境变量名 = FUNASR_SUBTITLE_<NAME>。空/非法值回退默认。

def _env(name: str) -> str | None:
    v = os.environ.get(f"FUNASR_SUBTITLE_{name}")
    return v if v not in (None, "") else None


def _env_int(name: str, default: int) -> int:
    v = _env(name)
    try:
        return int(v) if v is not None else default
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    v = _env(name)
    try:
        return float(v) if v is not None else default
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    v = _env(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


# 服务端口（找不到空闲会自增扫描）
DEFAULT_PORT = _env_int("PORT", 8765)
# 解码看门狗：连续这么多秒无进度判定卡死并杀 ffmpeg
STALL_TIMEOUT_S = _env_float("STALL_TIMEOUT", 90.0)
# 单条字幕最长时长（毫秒），专业规范一般 ≤7s
MAX_CUE_MS = _env_int("MAX_CUE_MS", 7000)
# 每行最大「显示宽度」默认值（0=不限制；UI 可逐任务调整）
DEFAULT_MAX_CHARS = _env_int("MAX_CHARS", 30)
# 上传大小上限（MB，0=不限制）
MAX_UPLOAD_MB = _env_int("MAX_UPLOAD_MB", 0)
# 内存里保留的已完成任务数上限：每个 Job 常驻全部 segments，不设限则长会话下无限增长。
# 超出后从最旧的终态任务开始丢弃（排队/执行中的任务永不丢弃）。
MAX_JOBS = _env_int("MAX_JOBS", 50)
# 常驻引擎缓存上限：每个引擎持有一组 ONNX session（单个模型数百 MB），切换模型若只增不减
# 会把内存吃光。任务在 worker 线程里串行执行，同时最多只有一个引擎在用，留 2 个足够。
ENGINE_CACHE_SIZE = max(1, _env_int("ENGINE_CACHE_SIZE", 2))
# onnxruntime 线程数（0=onnxruntime 默认）
NUM_THREADS = _env_int("NUM_THREADS", 4)
# ct-punc 长文本分块长度
PUNC_CHUNK = _env_int("PUNC_CHUNK", 110)


def data_dir() -> Path:
    """%LOCALAPPDATA%\\funasr-subtitle（可被 FUNASR_SUBTITLE_DATA_DIR 覆盖，便于测试）。"""
    override = os.environ.get("FUNASR_SUBTITLE_DATA_DIR")
    base = Path(override) if override else Path(user_data_dir(APP_NAME, APP_AUTHOR))
    base.mkdir(parents=True, exist_ok=True)
    return base


def models_dir() -> Path:
    d = data_dir() / "models"
    d.mkdir(parents=True, exist_ok=True)
    return d


def jobs_dir() -> Path:
    d = data_dir() / "jobs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def is_frozen() -> bool:
    """是否运行在 PyInstaller 冻结产物里。"""
    return getattr(sys, "frozen", False)


def bundle_dir() -> Path:
    """冻结时为 _internal 所在目录；开发时为仓库 backend/ 目录。"""
    if is_frozen():
        return Path(sys.executable).parent
    return Path(__file__).resolve().parents[1]


def ffmpeg_path() -> Path:
    """定位捆绑的 ffmpeg.exe。

    打包结构（§12）：funasr-subtitle/ffmpeg/ffmpeg.exe，与 funasr-subtitle.exe 同级。
    开发时回退到 packaging/bin/ffmpeg.exe，再回退到 PATH 上的 ffmpeg。
    """
    if is_frozen():
        cand = bundle_dir() / "ffmpeg" / "ffmpeg.exe"
        if cand.exists():
            return cand
    repo_root = Path(__file__).resolve().parents[2]
    cand = repo_root / "packaging" / "bin" / "ffmpeg.exe"
    if cand.exists():
        return cand
    return Path("ffmpeg")  # 依赖 PATH（开发机已装）
