"""模型下载/缓存管理（§5, §6A, §17.5）。

设计要点（§17 验证结论）：
- 用 modelscope.snapshot_download 下载到 %LOCALAPPDATA%\\funasr-subtitle\\models。
- **torch-free**：本模块只 import modelscope，绝不安装 torch-stub
  （modelscope 的 logger 用 find_spec('torch')，发现 torch 就会 import 真 torch_utils）。
  torch-stub 只在 engine 里、且在 modelscope 完成初始化之后安装。
- SenseVoice 仓库含 893MB 的 model.pt（PyTorch 权重），下载时用 ignore_patterns 跳过，
  只取 *.onnx + 配置/词表。
"""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Callable, Optional

from ..config import models_dir
from .registry import REGISTRY, ModelSpec, get_model

# 不需要的大文件（PyTorch 权重等），下载时跳过以省体积/时间
_IGNORE_PATTERNS = ["*.pt", "*.pth", "*.bin", "*.safetensors", "*.onnx_data.bak"]

# percent 0..100, downloaded_mb, total_mb
DownloadProgress = Callable[[float, float, float], None]


class ModelManager:
    # 类级、跨实例共享的「按 model_id」下载锁：API 与 worker 各自持有不同的
    # ModelManager 实例却指向同一缓存目录，必须共享锁，避免并发下载互踩。
    _locks_guard = threading.Lock()
    _locks: "dict[str, threading.Lock]" = {}

    def __init__(self, root: Optional[Path] = None) -> None:
        self.root = root or models_dir()

    @classmethod
    def _lock_for(cls, model_id: str) -> threading.Lock:
        with cls._locks_guard:
            lk = cls._locks.get(model_id)
            if lk is None:
                lk = threading.Lock()
                cls._locks[model_id] = lk
            return lk

    # ---- 路径与状态 ----
    def local_dir(self, model_id: str) -> Path:
        """modelscope 缓存约定：<root>/<repo>（repo 形如 owner/name）。"""
        spec = get_model(model_id)
        return self.root / spec.repo

    def is_downloaded(self, model_id: str) -> bool:
        spec = get_model(model_id)
        d = self.local_dir(model_id)
        if not (d.is_dir() and any(d.glob("*.onnx"))):
            return False
        # 辅助文件（如 SenseVoice 的 bpe 词表）也必须就位
        for fname, _src in spec.aux_files:
            if not (d / fname).exists():
                return False
        # 目录内复制文件（如 contextual 的 model_eb_quant.onnx）也必须就位
        for _src, dst in spec.local_copies:
            if not (d / dst).exists():
                return False
        return True

    def ensure_dir(self, model_id: str) -> Path:
        """返回本地模型目录；若缺失则同步下载（无进度回调）。"""
        if not self.is_downloaded(model_id):
            self.download(model_id)
        return self.local_dir(model_id)

    # ---- 下载 ----
    def download(self, model_id: str, progress: Optional[DownloadProgress] = None) -> Path:
        """同步下载。progress(percent, downloaded_mb, total_mb)。

        modelscope 自身的进度回调不稳定，这里用「后台线程下载 + 轮询目录大小」估算进度。
        """
        spec = get_model(model_id)
        target = self.local_dir(model_id)

        if self.is_downloaded(model_id):
            if progress:
                progress(100.0, spec.size_mb, spec.size_mb)
            return target

        # 串行化同一模型的下载：并发下载会往同一目录写同名文件、互相覆盖/读到半成品。
        lock = self._lock_for(model_id)
        with lock:
            # 双检：等锁期间别的线程可能已下完。
            if self.is_downloaded(model_id):
                if progress:
                    progress(100.0, spec.size_mb, spec.size_mb)
                return target
            return self._download_locked(spec, target, progress)

    def _download_locked(self, spec, target, progress):  # noqa: ANN001
        from modelscope.hub.snapshot_download import snapshot_download  # torch-free
        model_id = spec.id

        result: dict[str, object] = {}

        def _run() -> None:
            try:
                result["path"] = snapshot_download(
                    spec.repo,
                    cache_dir=str(self.root),
                    ignore_file_pattern=_IGNORE_PATTERNS,
                )
            except TypeError:
                # 旧/新版本参数名差异：回退到 ignore_patterns
                try:
                    result["path"] = snapshot_download(
                        spec.repo,
                        cache_dir=str(self.root),
                        ignore_patterns=_IGNORE_PATTERNS,
                    )
                except Exception as e:  # noqa: BLE001
                    result["error"] = e
            except Exception as e:  # noqa: BLE001
                result["error"] = e

        t = threading.Thread(target=_run, daemon=True)
        t.start()

        total_bytes = spec.size_mb * 1024 * 1024
        if progress:
            progress(0.0, 0.0, spec.size_mb)
        while t.is_alive():
            t.join(timeout=0.5)
            if progress:
                got = _dir_size(target)
                pct = min(99.0, got / total_bytes * 100.0) if total_bytes else 0.0
                progress(round(pct, 1), round(got / 1024 / 1024, 1), spec.size_mb)

        if "error" in result:
            raise RuntimeError(f"download failed for {model_id}: {result['error']!r}")

        # 补齐辅助文件（§17：SenseVoice-onnx 缺 bpe 词表）
        self._fetch_aux_files(spec, target)
        # 目录内复制/改名（§M8：contextual 的 model_eb_quant.onnx）
        self._make_local_copies(spec, target)

        if progress:
            got = _dir_size(target)
            progress(100.0, round(got / 1024 / 1024, 1), spec.size_mb)
        return target

    def _fetch_aux_files(self, spec: ModelSpec, target: Path) -> None:
        if not spec.aux_files:
            return
        import shutil

        from modelscope.hub.snapshot_download import snapshot_download  # torch-free

        for fname, src_repo in spec.aux_files:
            if (target / fname).exists():
                continue
            try:
                src_dir = Path(snapshot_download(
                    src_repo, cache_dir=str(self.root), allow_file_pattern=[fname]))
            except TypeError:
                src_dir = Path(snapshot_download(
                    src_repo, cache_dir=str(self.root), allow_patterns=[fname]))
            src = src_dir / fname
            if src.exists():
                shutil.copy2(src, target / fname)
            else:
                raise RuntimeError(
                    f"aux file {fname} not found in {src_repo} for {spec.id}")

    def _make_local_copies(self, spec: ModelSpec, target: Path) -> None:
        if not spec.local_copies:
            return
        import shutil

        for src_name, dst_name in spec.local_copies:
            src, dst = target / src_name, target / dst_name
            if dst.exists():
                continue
            if src.exists():
                shutil.copy2(src, dst)
            else:
                raise RuntimeError(
                    f"local copy source {src_name} missing in {spec.id}")

    # ---- 目录列举（给 /api/models） ----
    def status(self, model_id: str) -> bool:
        return self.is_downloaded(model_id)

    def all_specs(self) -> list[ModelSpec]:
        return list(REGISTRY.values())


def _dir_size(path: Path) -> float:
    if not path.exists():
        return 0.0
    total = 0
    for p in path.rglob("*"):
        if p.is_file():
            try:
                total += p.stat().st_size
            except OSError:
                pass
    return float(total)
