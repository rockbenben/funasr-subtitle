"""上游 Paraformer-online 的薄适配；每次录音独享模型前端与流式 cache。"""
from __future__ import annotations

import os

import numpy as np

from ..config import NUM_THREADS, model_cache_dir
from ..models import ModelManager
from ..models.registry import STREAMING_PARAFORMER
from . import backend
from .torch_device import disable_cuda, incompatible_cuda, torch_device


class LiveEngine:
    def __init__(self) -> None:
        self.cache: dict = {}
        self.full = backend() == "full"
        self.model = None

    def load(self) -> None:
        manager = ModelManager()
        spec = STREAMING_PARAFORMER
        if self.full:
            os.environ["MODELSCOPE_CACHE"] = str(model_cache_dir())
            from funasr import AutoModel

            device = torch_device()
            with manager.download_lock(spec.id):
                try:
                    self.model = AutoModel(model=spec.full_model, device=device,
                                           disable_update=True, disable_pbar=True)
                except RuntimeError as exc:
                    if device != "cuda:0" or not incompatible_cuda(exc):
                        raise
                    disable_cuda(exc)
                    self.model = AutoModel(model=spec.full_model, device="cpu",
                                           disable_update=True, disable_pbar=True)
        else:
            from .funasr_engine import _ensure_funasr

            _ensure_funasr()
            from funasr_onnx.paraformer_online_bin import Paraformer

            self.model = Paraformer(str(manager.local_dir(spec.id)), quantize=True,
                                    chunk_size=[5, 10, 5], intra_op_num_threads=NUM_THREADS or 4)

    def feed(self, wav: np.ndarray, *, final: bool = False) -> str:
        if self.full:
            result = self.model.generate(input=wav, cache=self.cache, is_final=final,
                                         chunk_size=[0, 10, 5], encoder_chunk_look_back=4,
                                         decoder_chunk_look_back=1)
            return "".join(item.get("text", "") for item in result)
        result = self.model(wav, param_dict={"cache": self.cache, "is_final": final})
        return "".join(item["preds"][0] for item in result if item.get("preds"))
