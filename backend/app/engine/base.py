"""ASREngine 抽象接口（§10）。

设计目标：
- 模型、execution provider（CPU / DirectML）可替换。
- 管线各阶段（decode/vad/asr/punc/diar/assembling）通过 ProgressCallback 上报进度，
  同一个回调既给 CLI 打印，也给 WS 推送（§7）。
"""
from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Literal, Protocol

import numpy as np

from ..schemas import JobOptions, Segment

Stage = Literal["decoding", "vad", "asr", "punc", "diar", "assembling"]

# (stage, percent 0..100) -> None
ProgressCallback = Callable[[Stage, float], None]


def _noop(stage: Stage, percent: float) -> None:  # pragma: no cover
    pass


class EngineOptions:
    """引擎构造参数（与 JobOptions 区分：这是加载期配置）。"""

    def __init__(
        self,
        model_id: str,
        models_root: str,
        use_gpu: bool = False,  # DirectML EP，§10，需验证可用性
        num_threads: int = 0,  # 0 -> onnxruntime 默认
        diarization: bool = False,  # 说话人分离（仅 full 后端 + Paraformer 生效）
    ) -> None:
        self.model_id = model_id
        self.models_root = models_root
        self.use_gpu = use_gpu
        self.num_threads = num_threads
        self.diarization = diarization


class ASREngine(Protocol):
    """统一推理接口。具体实现见 funasr_engine.py。"""

    def load(self) -> None:
        """加载/初始化所有需要的 ONNX 模型（VAD/ASR/punc/...）。"""
        ...

    def transcribe(
        self,
        wav: np.ndarray,
        sample_rate: int,
        options: JobOptions,
        progress: ProgressCallback | None = None,
        cancel: threading.Event | None = None,
    ) -> list[Segment]:
        """对 16k 单声道 PCM（float32, -1..1）转写，返回带时间戳的 segments。

        cancel：传入后，置位即在分段边界尽快停止（已转写部分会被上层按取消丢弃）。
        """
        ...
