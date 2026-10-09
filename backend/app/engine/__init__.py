"""推理引擎层（§10）。

把推理封装在 ASREngine 抽象后面，让模型 / execution provider 可替换。
§17 验证结论见本目录 README.md。
"""
from __future__ import annotations

import os

from ..config import NUM_THREADS, models_dir
from ..models import DEFAULT_MODEL_ID
from .base import ASREngine, EngineOptions, ProgressCallback, Stage


def backend() -> str:
    """推理后端：'onnx'（默认，funasr-onnx 纯 CPU）| 'full'（完整 funasr + torch/CUDA）。

    优先级：环境变量 FUNASR_SUBTITLE_BACKEND（便于同机切换测试）> 构建期烘焙的
    app/_build.py（由 build.ps1 -Variant 写入）> 默认 onnx。
    """
    env = os.environ.get("FUNASR_SUBTITLE_BACKEND")
    if env:
        return env.strip().lower()
    try:
        from .. import _build  # 构建期生成，未提交
        return str(getattr(_build, "BACKEND", "onnx")).strip().lower()
    except Exception:  # noqa: BLE001  # _build.py 不存在（开发态）是正常路径，回退 onnx
        return "onnx"


def compute_label() -> str:
    """供 UI 显示的算力标签：full + CUDA -> 'GPU · CUDA'，否则 'CPU'。"""
    if backend() == "full":
        try:
            import torch
            if torch.cuda.is_available():
                return "GPU · CUDA"
        except Exception:  # noqa: BLE001  # onnx 包根本没装 torch，查不到就当 CPU
            pass
    return "CPU"


def build_engine(
    model_id: str = "",
    *,
    models_root: str | None = None,
    use_gpu: bool = False,
    num_threads: int = 0,
    diarization: bool = False,
) -> ASREngine:
    """按后端构造引擎实例（onnx -> FunasrEngine；full -> FunasrFullEngine）。

    num_threads 未显式指定（<=0）时用 config.NUM_THREADS（env FUNASR_SUBTITLE_NUM_THREADS）。
    """
    opts = EngineOptions(
        model_id=model_id or DEFAULT_MODEL_ID,
        models_root=models_root or str(models_dir()),
        use_gpu=use_gpu,
        num_threads=num_threads if num_threads and num_threads > 0 else NUM_THREADS,
        diarization=diarization,
    )
    if backend() == "full":
        from .funasr_full_engine import FunasrFullEngine
        return FunasrFullEngine(opts)
    from .funasr_engine import FunasrEngine
    return FunasrEngine(opts)


__all__ = [
    "ASREngine",
    "EngineOptions",
    "ProgressCallback",
    "Stage",
    "build_engine",
]
