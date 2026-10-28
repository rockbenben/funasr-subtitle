"""模型缓存布局与下载落点的护栏（修复 issue #4）。

背景：modelscope ≥1.38 默认把仓库放到 {cache}/models/<owner>--<name>/snapshots/<rev>，
而应用按 {cache}/<owner>/<name> 找模型。v0.2.2 因此出现「下完 230MB 后补 bpe 时
FileNotFoundError，重试必现，但磁盘上文件都在」。

这里把三件事钉住：
1. 两种布局都能被识别（老用户不必重下 230MB）；
2. onnx 下载显式传 local_dir，文件落回平铺目录；
3. 完整后端的 pt 模型下载同样落平铺目录（funasr 会当 legacy cache 复用）。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.config import model_cache_dir
from app.models import manager as manager_mod
from app.models.manager import ModelManager
from app.models.registry import (
    FULL_PUNC_REPO,
    FULL_VAD_REPO,
    FULL_VAD_SIZE_MB,
    PARAFORMER_ZH,
    PARAFORMER_ZH_HOTWORD,
    SENSEVOICE,
)

BPE = "chn_jpn_yue_eng_ko_spectok.bpe.model"


def _touch(path: Path, data: bytes = b"x") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def _snapshot_dir(root: Path, repo: str) -> Path:
    return root / "models" / repo.replace("/", "--") / "snapshots" / "master"


def _fake_snapshot(calls: list[dict], oni: bool = True):
    """模拟 snapshot_download：把文件写进 local_dir，并记录调用参数。"""

    def fake(repo, root, local_dir, *, ignore=None, allow=None):
        calls.append({"repo": repo, "local_dir": Path(local_dir), "ignore": ignore, "allow": allow})
        if allow:
            for name in allow:
                _touch(Path(local_dir) / name)
        elif oni:
            _touch(Path(local_dir) / "model_quant.onnx")
        else:
            _touch(Path(local_dir) / "model.pt")
            _touch(Path(local_dir) / "config.yaml")
        return Path(local_dir)

    return fake


def test_model_cache_dir_honors_modelscope_cache(tmp_path, monkeypatch):
    """用户设了 MODELSCOPE_CACHE 时，应用下载和 funasr 必须跟着它走，不能各存一份。"""
    monkeypatch.setenv("MODELSCOPE_CACHE", str(tmp_path / "ms-cache"))
    assert model_cache_dir() == tmp_path / "ms-cache"

    monkeypatch.delenv("MODELSCOPE_CACHE")
    monkeypatch.setenv("FUNASR_SUBTITLE_DATA_DIR", str(tmp_path / "data"))
    assert model_cache_dir() == tmp_path / "data" / "models"


def test_engine_and_manager_share_cache_root(tmp_path, monkeypatch):
    """下载面板和引擎必须看同一个缓存根，否则「下完还是未下载 / 引擎又下一遍」。"""
    monkeypatch.setenv("MODELSCOPE_CACHE", str(tmp_path / "cache"))
    from app.engine import build_engine

    assert ModelManager().root == tmp_path / "cache"
    assert build_engine().opts.models_root == str(tmp_path / "cache")


def test_flat_cache_is_detected(tmp_path):
    mgr = ModelManager(root=tmp_path)
    flat = tmp_path / SENSEVOICE.repo
    _touch(flat / "model_quant.onnx")
    _touch(flat / BPE)
    assert mgr.local_dir(SENSEVOICE.id) == flat
    assert mgr.is_downloaded(SENSEVOICE.id) is True


def test_new_layout_snapshot_is_detected(tmp_path):
    snap = _snapshot_dir(tmp_path, SENSEVOICE.repo)
    _touch(snap / "model_quant.onnx")
    _touch(snap / BPE)
    mgr = ModelManager(root=tmp_path)
    assert mgr.local_dir(SENSEVOICE.id) == snap
    assert mgr.is_downloaded(SENSEVOICE.id) is True


def test_partial_onnx_download_is_not_ready(tmp_path):
    """半截仓库（只有 model_eb.onnx / .incomplete）不能算已下载，否则引擎加载会缺主模型。"""
    mgr = ModelManager(root=tmp_path)
    flat = tmp_path / SENSEVOICE.repo
    _touch(flat / "model_eb.onnx")
    _touch(flat / BPE)
    _touch(flat / "model_quant.onnx.incomplete")
    assert mgr.is_downloaded(SENSEVOICE.id) is False

    _touch(flat / "model_quant.onnx")
    assert mgr.is_downloaded(SENSEVOICE.id) is True


def test_old_modelscope_cache_layout_is_detected(tmp_path):
    """旧 SDK 的 {cache}/models/{owner}/{name} 布局也要认，别让老缓存被判定成未下载。"""
    old = tmp_path / "models" / "iic" / "SenseVoiceSmall-onnx"
    _touch(old / "model_quant.onnx")
    _touch(old / BPE)
    mgr = ModelManager(root=tmp_path)
    assert mgr.local_dir(SENSEVOICE.id) == old
    assert mgr.is_downloaded(SENSEVOICE.id) is True


def test_download_fixes_flat_local_dir(tmp_path, monkeypatch):
    calls: list[dict] = []
    monkeypatch.setattr(manager_mod, "_snapshot", _fake_snapshot(calls))
    mgr = ModelManager(root=tmp_path)

    out = mgr.download(SENSEVOICE.id)

    flat = tmp_path / SENSEVOICE.repo
    assert out == flat
    assert mgr.is_downloaded(SENSEVOICE.id) is True
    # 主仓库必须显式钉 local_dir（否则 modelscope 又落到新布局）
    assert calls[0]["repo"] == SENSEVOICE.repo
    assert calls[0]["local_dir"] == flat
    assert calls[0]["ignore"] == manager_mod._IGNORE_PATTERNS
    # aux 也直接落进模型目录
    assert calls[1]["repo"] == "iic/SenseVoiceSmall"
    assert calls[1]["local_dir"] == flat
    assert calls[1]["allow"] == [BPE]
    assert (flat / BPE).exists()


def test_new_layout_cache_is_healed_without_redownload(tmp_path, monkeypatch):
    """v0.2.2 已下完 230MB、只缺 bpe 的现场：原地补文件，不换目录重下。"""
    snap = _snapshot_dir(tmp_path, SENSEVOICE.repo)
    _touch(snap / "model_quant.onnx")
    calls: list[dict] = []
    monkeypatch.setattr(manager_mod, "_snapshot", _fake_snapshot(calls))
    mgr = ModelManager(root=tmp_path)

    out = mgr.download(SENSEVOICE.id)

    assert out == snap
    assert calls[0]["local_dir"] == snap
    assert (snap / BPE).exists()
    assert mgr.local_dir(SENSEVOICE.id) == snap
    assert mgr.is_downloaded(SENSEVOICE.id) is True


def test_new_layout_contextual_adds_local_copies(tmp_path, monkeypatch):
    """热词版在新布局里也要原地补出 model_eb_quant.onnx（重下 871MB 是不可接受的）。"""
    spec = PARAFORMER_ZH_HOTWORD
    snap = _snapshot_dir(tmp_path, spec.repo)
    _touch(snap / "model_quant.onnx")
    _touch(snap / "model_eb.onnx")
    calls: list[dict] = []
    monkeypatch.setattr(manager_mod, "_snapshot", _fake_snapshot(calls))
    mgr = ModelManager(root=tmp_path)

    assert mgr.is_downloaded(spec.id) is False
    out = mgr.download(spec.id)

    assert out == snap
    assert calls[0]["local_dir"] == snap
    assert (snap / "model_eb_quant.onnx").exists()
    assert mgr.is_downloaded(spec.id) is True


def test_full_downloaded_recognizes_new_layout(tmp_path):
    """v0.2.2 完整版懒下载留下的新布局缓存，升级后不能再让用户重下 900MB。"""
    for repo in (SENSEVOICE.full_repo, FULL_VAD_REPO):
        snap = _snapshot_dir(tmp_path, repo)
        _touch(snap / "model.pt")
        _touch(snap / "config.yaml")
    mgr = ModelManager(root=tmp_path)
    assert mgr.full_downloaded(SENSEVOICE.id) is True


def test_full_download_lands_in_flat_dirs(tmp_path, monkeypatch):
    calls: list[dict] = []
    monkeypatch.setattr(manager_mod, "_snapshot", _fake_snapshot(calls, oni=False))
    mgr = ModelManager(root=tmp_path)

    assert mgr.full_downloaded(SENSEVOICE.id) is False
    mgr.download_full(SENSEVOICE.id)

    # SenseVoice 自带标点 -> 只下 ASR + VAD
    assert [c["repo"] for c in calls] == [SENSEVOICE.full_repo, FULL_VAD_REPO]
    assert calls[0]["local_dir"] == tmp_path / SENSEVOICE.full_repo
    assert calls[1]["local_dir"] == tmp_path / FULL_VAD_REPO
    assert mgr.full_downloaded(SENSEVOICE.id) is True
    assert mgr.full_total_mb(SENSEVOICE.id) == pytest.approx(
        SENSEVOICE.full_size_mb + FULL_VAD_SIZE_MB)


def test_full_download_paraformer_includes_punc(tmp_path, monkeypatch):
    calls: list[dict] = []
    monkeypatch.setattr(manager_mod, "_snapshot", _fake_snapshot(calls, oni=False))
    mgr = ModelManager(root=tmp_path)

    mgr.download_full(PARAFORMER_ZH.id)

    assert [c["repo"] for c in calls] == [PARAFORMER_ZH.full_repo, FULL_VAD_REPO, FULL_PUNC_REPO]
    assert mgr.full_downloaded(PARAFORMER_ZH.id) is True


def test_full_download_reuses_cached_repo(tmp_path, monkeypatch):
    """ASR 已在（新布局），只补 VAD；不能再把 953MB 重下一遍。"""
    snap = _snapshot_dir(tmp_path, PARAFORMER_ZH.full_repo)
    _touch(snap / "model.pt")
    _touch(snap / "config.yaml")
    calls: list[dict] = []
    monkeypatch.setattr(manager_mod, "_snapshot", _fake_snapshot(calls, oni=False))
    mgr = ModelManager(root=tmp_path)

    mgr.download_full(PARAFORMER_ZH.id)

    assert [c["repo"] for c in calls] == [FULL_VAD_REPO, FULL_PUNC_REPO]
    assert mgr.full_downloaded(PARAFORMER_ZH.id) is True
