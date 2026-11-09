"""只在 full 后端导入：CUDA 可见并不代表随包的内核支持当前显卡。"""
from __future__ import annotations

import logging
import os

_cuda_usable: bool | None = None
_log = logging.getLogger(__name__)


def incompatible_cuda(exc: RuntimeError) -> bool:
    message = str(exc).lower()
    return ("no kernel image is available for execution on the device" in message
            or "invalid device function" in message)


def disable_cuda(exc: RuntimeError) -> None:
    global _cuda_usable
    _cuda_usable = False
    _log.warning("当前 CUDA 构建不支持这张显卡，改用 CPU：%s", exc)


def torch_device() -> str:
    global _cuda_usable
    if os.environ.get("FUNASR_SUBTITLE_FORCE_CPU") == "1":
        return "cpu"
    import torch

    if _cuda_usable is None:
        if not torch.cuda.is_available():
            _cuda_usable = False
        else:
            try:
                # is_available 只证明驱动可见；实际启动内核并同步，提前暴露架构不兼容。
                probe = torch.ones(1, device="cuda:0")
                probe.add_(1)
                torch.cuda.synchronize()
                _cuda_usable = True
            except RuntimeError as exc:
                if not incompatible_cuda(exc):
                    raise
                disable_cuda(exc)
    return "cuda:0" if _cuda_usable else "cpu"
