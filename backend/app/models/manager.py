"""模型下载/缓存管理（§5, §6A, §17.5）。

设计要点：
- 模型统一落到**平铺**目录 <root>/<repo>。<root> 默认是 %LOCALAPPDATA%\\funasr-subtitle\\models；
  用户设了 MODELSCOPE_CACHE 就改用它（funasr 读同一个变量，下载面板和引擎不会各存一份）。
- modelscope ≥1.38 默认改成 <root>/models/<owner>--<name>/snapshots/<rev> 的新布局；
  下载时显式传 local_dir 固定平铺布局，读取时两种布局都认——已经按新布局下过模型的
  用户不必重下（v0.2.2 踩过：下载完 230MB 后按旧布局找目录，aux 复制报 FileNotFoundError）。
- 完整版（funasr/torch）的 pt 模型也预置到同样的平铺目录：funasr 的 snapshot_download
  会把 <cache>/<owner>/<name> 当作 legacy cache 直接复用。
- **torch-free**：本模块只 import modelscope，绝不安装 torch-stub
  （modelscope 的 logger 用 find_spec('torch')，发现 torch 就会 import 真 torch_utils）。
- SenseVoice 仓库含 893MB 的 model.pt（PyTorch 权重），onnx 下载时用 ignore_patterns 跳过。
"""
from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path
from typing import ClassVar

from ..config import model_cache_dir
from .registry import (
    FULL_PUNC_REPO,
    FULL_PUNC_SIZE_MB,
    FULL_VAD_REPO,
    FULL_VAD_SIZE_MB,
    REGISTRY,
    ModelSpec,
    get_model,
)

# funasr-onnx 的 quantize=True 路径固定加载这个文件名；用它当「主模型已就位」的判据，
# 不能用 any(*.onnx)：local_dir 下载时文件逐个落盘，半截仓库里可能已有 model_eb.onnx。
_ONNX_MAIN = "model_quant.onnx"

# onnx 不需要的大文件（PyTorch 权重等），下载时跳过以省体积/时间
_IGNORE_PATTERNS = ["*.pt", "*.pth", "*.bin", "*.safetensors", "*.onnx_data.bak"]

# 完整后端的模型目录至少要有的文件（funasr 加载所需）
_FULL_REQUIRED = ("model.pt", "config.yaml")

# percent 0..100, downloaded_mb, total_mb
DownloadProgress = Callable[[float, float, float], None]


def _snapshot(
    repo: str,
    root: Path,
    local_dir: Path,
    *,
    ignore: list[str] | None = None,
    allow: list[str] | None = None,
) -> Path:
    """snapshot_download 薄封装：固定落到 local_dir，旧版 modelscope 退回旧参数名。"""
    from modelscope.hub.snapshot_download import snapshot_download  # torch-free

    kwargs: dict = {"cache_dir": str(root), "local_dir": str(local_dir)}
    if ignore:
        kwargs["ignore_file_pattern"] = list(ignore)
    if allow:
        kwargs["allow_file_pattern"] = list(allow)
    try:
        return Path(snapshot_download(repo, **kwargs))
    except TypeError:
        legacy: dict = {"cache_dir": str(root)}
        if ignore:
            legacy["ignore_patterns"] = list(ignore)
        if allow:
            legacy["allow_patterns"] = list(allow)
        return Path(snapshot_download(repo, **legacy))


def _has_full_files(d: Path) -> bool:
    return all((d / name).exists() for name in _FULL_REQUIRED)


class ModelManager:
    # 类级、跨实例共享的「按 model_id」下载锁：API 与 worker 各自持有不同的
    # ModelManager 实例却指向同一缓存目录，必须共享锁，避免并发下载互踩。
    _locks_guard = threading.Lock()
    _locks: ClassVar[dict[str, threading.Lock]] = {}

    def __init__(self, root: Path | None = None) -> None:
        self.root = root or model_cache_dir()

    @classmethod
    def download_lock(cls, model_id: str) -> threading.Lock:
        """按 model_id 的下载锁；完整版引擎加载前也拿它，避免和面板下载并发写同一目录。"""
        with cls._locks_guard:
            lk = cls._locks.get(model_id)
            if lk is None:
                lk = threading.Lock()
                cls._locks[model_id] = lk
            return lk

    # ---- 路径与状态 ----
    def _candidates(self, repo: str) -> list[Path]:
        """仓库可能的落盘位置：平铺目录 → 新布局快照 → modelscope 旧缓存布局。"""
        out = [self.root / repo]
        snapshots = self.root / "models" / repo.replace("/", "--") / "snapshots"
        if snapshots.is_dir():
            snaps = [p for p in snapshots.iterdir() if p.is_dir()]
            snaps.sort(key=_mtime, reverse=True)
            out.extend(snaps)
        # 旧 SDK 的布局：{cache}/models/{owner}/{safe_name}（显式 MODELSCOPE_CACHE）
        # 与 {cache}/hub/{owner}/{safe_name}。funasr 的 legacy 探测也会找这两处。
        owner, _, name = repo.partition("/")
        safe = name.replace(".", "___")
        out.extend([self.root / "models" / owner / safe, self.root / "hub" / owner / safe])
        return out

    def _find_dir(self, repo: str, ok: Callable[[Path], bool]) -> Path | None:
        for d in self._candidates(repo):
            if d.is_dir() and ok(d):
                return d
        return None

    def local_dir(self, model_id: str) -> Path:
        """模型实际所在目录：平铺目录优先，其次新布局快照；都没有则返回平铺默认位置。"""
        spec = get_model(model_id)
        found = self._find_dir(spec.repo, lambda d: (d / _ONNX_MAIN).is_file())
        return found or (self.root / spec.repo)

    def is_downloaded(self, model_id: str) -> bool:
        spec = get_model(model_id)
        d = self.local_dir(model_id)
        if not (d / _ONNX_MAIN).is_file():
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

    # ---- onnx 模型下载 ----
    def download(self, model_id: str, progress: DownloadProgress | None = None) -> Path:
        """同步下载。progress(percent, downloaded_mb, total_mb)。

        modelscope 自身的进度回调不稳定，这里用「后台线程下载 + 轮询目录大小」估算进度。
        """
        spec = get_model(model_id)
        if self.is_downloaded(model_id):
            if progress:
                progress(100.0, spec.size_mb, spec.size_mb)
            return self.local_dir(model_id)

        # 串行化同一模型的下载：并发下载会往同一目录写同名文件、互相覆盖/读到半成品。
        lock = self.download_lock(model_id)
        with lock:
            # 双检：等锁期间别的线程可能已下完。
            if self.is_downloaded(model_id):
                if progress:
                    progress(100.0, spec.size_mb, spec.size_mb)
                return self.local_dir(model_id)
            return self._download_locked(spec, self.local_dir(model_id), progress)

    def _download_locked(self, spec: ModelSpec, target: Path, progress: DownloadProgress | None) -> Path:
        model_id = spec.id
        target.mkdir(parents=True, exist_ok=True)
        result: dict[str, object] = {}

        def _run() -> None:
            try:
                result["path"] = _snapshot(spec.repo, self.root, target, ignore=_IGNORE_PATTERNS)
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

        for fname, src_repo in spec.aux_files:
            dst = target / fname
            if dst.exists():
                continue
            target.mkdir(parents=True, exist_ok=True)
            # local_dir + allow：辅助文件直接落进模型目录；旧版忽略 local_dir 时退回复制。
            src_dir = _snapshot(src_repo, self.root, target, allow=[fname])
            if dst.exists():
                continue
            src = src_dir / fname
            if src.exists():
                shutil.copy2(src, dst)
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

    # ---- 完整后端（pt 模型）下载 ----
    def _full_parts(self, spec: ModelSpec) -> list[tuple[str, float]]:
        """完整后端跑这个模型要预置的 pt 权重：ASR 本体 + VAD（+ 不自带标点时的 ct-punc）。

        刻意**不含 cam++**（说话人分离模型）：它只在勾选了「区分说话人」时才用到，
        体积不大，让 funasr 在加载引擎时自己按需下载即可，预置清单不必背这个锅。
        """
        if not spec.full_repo:
            return []
        parts = [(spec.full_repo, spec.full_size_mb), (FULL_VAD_REPO, FULL_VAD_SIZE_MB)]
        if not spec.builtin_punc:
            parts.append((FULL_PUNC_REPO, FULL_PUNC_SIZE_MB))
        return parts

    def full_total_mb(self, model_id: str) -> float:
        """完整后端跑这个模型要下载的总量（ASR + VAD + 必要时 punc），用于 UI 展示体积。"""
        return sum(size for _repo, size in self._full_parts(get_model(model_id)))

    def full_downloaded(self, model_id: str) -> bool:
        """完整后端「模型已就位」的判据：_full_parts 里每个 pt 仓库都含 model.pt + config.yaml。"""
        parts = self._full_parts(get_model(model_id))
        return bool(parts) and all(
            self._find_dir(repo, _has_full_files) is not None for repo, _size in parts
        )

    def download_full(self, model_id: str, progress: DownloadProgress | None = None) -> None:
        """下载完整后端所需的 pt 模型，落到平铺目录供 funasr 复用。

        落平铺布局（<root>/<owner>/<name>）是刻意的：funasr 的 legacy cache 探测会把它
        当现成缓存直接用，下载面板和引擎因此只存一份。
        没有这一步也能跑——引擎加载时 funasr 会自己按需下载（走 ModelScope 的新布局），
        但那时没有进度可报，界面只能干等。
        """
        spec = get_model(model_id)
        parts = self._full_parts(spec)
        if not parts:
            raise RuntimeError(f"model {model_id} does not support the full backend")
        total_mb = sum(size for _repo, size in parts)

        if self.full_downloaded(model_id):
            if progress:
                progress(100.0, total_mb, total_mb)
            return

        lock = self.download_lock(model_id)
        with lock:
            if self.full_downloaded(model_id):
                if progress:
                    progress(100.0, total_mb, total_mb)
                return
            targets = {
                repo: self._find_dir(repo, _has_full_files) or (self.root / repo)
                for repo, _size in parts
            }
            for target in targets.values():
                target.mkdir(parents=True, exist_ok=True)
            pending = [(repo, targets[repo]) for repo, _size in parts
                       if self._find_dir(repo, _has_full_files) is None]
            pending_mb = sum(size for repo, size in parts
                             if repo in {r for r, _t in pending})
            result: dict[str, object] = {}

            def _run() -> None:
                try:
                    for repo, target in pending:
                        _snapshot(repo, self.root, target)
                except Exception as e:  # noqa: BLE001
                    result["error"] = e

            t = threading.Thread(target=_run, daemon=True)
            t.start()
            if progress:
                progress(0.0, 0.0, total_mb)
            while t.is_alive():
                t.join(timeout=0.5)
                if progress:
                    got = sum(_dir_size(d) for d in targets.values()) / 1024 / 1024
                    pct = min(99.0, got / pending_mb * 100.0) if pending_mb else 0.0
                    progress(round(pct, 1), round(got, 1), total_mb)

            if "error" in result:
                raise RuntimeError(f"download failed for {model_id}: {result['error']!r}")
            if not self.full_downloaded(model_id):
                raise RuntimeError(f"download incomplete for {model_id}")
            if progress:
                got = sum(_dir_size(d) for d in targets.values()) / 1024 / 1024
                progress(100.0, round(got, 1), total_mb)

    # ---- 遗留 API（当前无人调用） ----
    def status(self, model_id: str) -> bool:
        """遗留接口：等价于 is_downloaded()。

        注意它**只看 onnx 仓库**，在 full 后端下会误报「没下好」——
        /api/models 因此不调它，而是按后端分别走 full_downloaded() / is_downloaded()。
        """
        return self.is_downloaded(model_id)

    def all_specs(self) -> list[ModelSpec]:
        """遗留接口：全部模型（含 VAD/punc 辅助模型）。/api/models 只暴露 ASR_MODELS。"""
        return list(REGISTRY.values())


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


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
