"""自动发布必须挡住版本错配、坏分卷、损坏文件和不完整上传；测试不访问 GitHub。"""
import importlib.util
import io
import json
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SHA = "a" * 40
VERSION = json.loads((ROOT / "frontend/package.json").read_text(encoding="utf-8"))["version"]
TAG = f"v{VERSION}"


@pytest.fixture()
def release():
    spec = importlib.util.spec_from_file_location("release_validation", ROOT / "packaging/release.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def assets(tmp_path, release):
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as archive:
        for name in ("funasr-subtitle.exe", "README.txt", "ffmpeg/ffmpeg.exe", "ffmpeg/ffprobe.exe"):
            archive.writestr(f"funasr-subtitle/{name}", b"fixture")
    raw = data.getvalue()
    (tmp_path / f"{release.STEMS['onnx']}.zip").write_bytes(raw)
    stem = release.STEMS["full"]
    midpoint = len(raw) // 2
    for number, chunk in enumerate((raw[:midpoint], raw[midpoint:]), 1):
        (tmp_path / f"{stem}.zip.{number:03d}").write_bytes(chunk)
    (tmp_path / f"{stem}-merge.bat").write_text(
        f'copy /b "{stem}.zip.001"+"{stem}.zip.002" "{stem}.zip"', encoding="ascii")
    release.check_assets(tmp_path, "both", write=True)
    return tmp_path


def test_current_version_matches_all_metadata(release):
    release.check_version(ROOT, TAG)


@pytest.mark.parametrize("tag", ["v9999.0.0", "v0.5.0-rc1", "main", "v0.5.0;echo", "v00.5.0"])
def test_invalid_or_mismatched_tag_fails(release, tag):
    with pytest.raises(ValueError):
        release.check_version(ROOT, tag)


@pytest.mark.parametrize("file", ["backend/app/__init__.py", "backend/pyproject.toml",
                                  "frontend/package.json", "frontend/package-lock.json"])
def test_each_version_source_is_checked(release, tmp_path, file):
    for name in ("backend/app/__init__.py", "backend/pyproject.toml",
                 "frontend/package.json", "frontend/package-lock.json"):
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        text = (ROOT / name).read_text(encoding="utf-8")
        target.write_text(text.replace(VERSION, "9999.0.0") if name == file else text, encoding="utf-8")
    with pytest.raises(ValueError):
        release.check_version(tmp_path, TAG)


def test_valid_assets_and_checksums_pass(release, assets):
    assert len(release.check_assets(assets, "both")) == 6


@pytest.mark.parametrize("damage", ["missing_volume", "changed_bytes", "mixed_zip", "stale_volume", "extra"])
def test_damaged_assets_fail(release, assets, damage):
    stem = release.STEMS["full"]
    if damage == "missing_volume":
        (assets / f"{stem}.zip.001").unlink()
    elif damage == "changed_bytes":
        (assets / f"{stem}.zip.002").write_bytes(b"corrupted")
    elif damage == "mixed_zip":
        (assets / f"{stem}.zip").write_bytes(b"stale")
    elif damage == "stale_volume":
        (assets / f"{stem}.zip.003").write_bytes(b"stale")
    else:
        (assets / "unexpected.exe").write_bytes(b"stale")
    with pytest.raises(ValueError):
        release.check_assets(assets, "both")


def test_corrupt_zip_cannot_get_a_checksum_manifest(release, assets):
    (assets / f"{release.STEMS['onnx']}.zip").write_bytes(b"not a ZIP")
    with pytest.raises(zipfile.BadZipFile):
        release.check_assets(assets, "onnx", write=True)


def _remote_assets(release, assets):
    return [{"name": path.name, "size": path.stat().st_size, "state": "uploaded",
             "digest": f"sha256:{release.digest(path)}"}
            for path in release.check_assets(assets, "both")]


@pytest.mark.parametrize("failure", [None, "missing_asset", "wrong_digest", "tag_moved", "unmanaged_draft"])
def test_publish_only_after_every_upload_is_verified(release, assets, monkeypatch, failure):
    calls = []
    state = {"created": False, "uploaded": False, "tag_checks": 0}
    monkeypatch.setenv("GH_REPO", "owner/repo")

    def gh(*args, missing_ok=False):
        calls.append(args)
        if args[0] == "api" and "/commits/" in args[1]:
            state["tag_checks"] += 1
            moved = failure == "tag_moved" and state["tag_checks"] > 1
            return json.dumps({"sha": "b" * 40 if moved else SHA})
        if args[:2] == ("release", "create"):
            state["created"] = True
        elif args[:2] == ("release", "upload"):
            state["uploaded"] = True
        elif args[0] == "api":
            if failure == "unmanaged_draft":
                return json.dumps({"draft": True, "body": "user draft", "assets": []})
            if not state["created"]:
                assert args[1].endswith("/releases") and "--paginate" in args
                return ""
            remote = _remote_assets(release, assets)
            if failure == "missing_asset":
                remote.pop()
            if failure == "wrong_digest":
                remote[0]["digest"] = "sha256:" + "0" * 64
            return json.dumps({"draft": True, "assets": remote})
        return ""

    monkeypatch.setattr(release, "gh", gh)
    if failure:
        with pytest.raises(ValueError):
            release.publish(ROOT, assets, TAG, SHA)
        assert not any(c[:2] == ("release", "edit") for c in calls)
    else:
        release.publish(ROOT, assets, TAG, SHA)
        assert calls[-1] == ("release", "edit", TAG, "--draft=false", "--verify-tag")
        assert state["uploaded"]


def test_published_release_is_idempotent(release, assets, monkeypatch):
    monkeypatch.setenv("GH_REPO", "owner/repo")
    calls = []

    def gh(*args, **kwargs):
        calls.append(args)
        return json.dumps({"sha": SHA} if "/commits/" in args[1] else
                          {"draft": False, "assets": _remote_assets(release, assets)})

    monkeypatch.setattr(release, "gh", gh)
    release.publish(ROOT, assets, TAG, SHA)
    assert all(call[0] == "api" for call in calls)


def test_partial_ci_draft_is_retried_without_approval(release, assets, monkeypatch):
    monkeypatch.setenv("GH_REPO", "owner/repo")
    calls = []
    uploaded = False

    def gh(*args, **kwargs):
        nonlocal uploaded
        calls.append(args)
        if args[:2] == ("release", "upload"):
            uploaded = True
        elif args[0] == "api":
            if "/commits/" in args[1]:
                return json.dumps({"sha": SHA})
            return json.dumps({"draft": True, "body": f"<!-- funasr-subtitle-ci:{SHA} -->",
                               "assets": _remote_assets(release, assets) if uploaded else []})
        return ""

    monkeypatch.setattr(release, "gh", gh)
    release.publish(ROOT, assets, TAG, SHA)
    assert not any(call[:2] == ("release", "create") for call in calls)
    assert calls[-1][:2] == ("release", "edit")
