"""Validate release inputs and publish only a complete, checksum-verified CI build."""
import argparse
import ast
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import tomllib
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STEMS = {"onnx": "funasr-subtitle-win-x64", "full": "funasr-subtitle-cuda-win-x64"}


def check_version(root: Path, tag: str) -> None:
    if not re.fullmatch(r"v(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)", tag):
        raise ValueError("Release tag must be a stable version such as v0.5.0")
    version = tag[1:]
    tree = ast.parse((root / "backend/app/__init__.py").read_text(encoding="utf-8"))
    app_version = next(ast.literal_eval(node.value) for node in tree.body
                       if isinstance(node, ast.Assign)
                       and any(isinstance(t, ast.Name) and t.id == "__version__" for t in node.targets))
    versions = {
        "backend/app/__init__.py": app_version,
        "backend/pyproject.toml": tomllib.loads(
            (root / "backend/pyproject.toml").read_text(encoding="utf-8"))["project"]["version"],
        "frontend/package.json": json.loads(
            (root / "frontend/package.json").read_text(encoding="utf-8"))["version"],
    }
    lock = json.loads((root / "frontend/package-lock.json").read_text(encoding="utf-8"))
    versions.update({
        "package-lock root": lock["version"], "package-lock package": lock["packages"][""]["version"],
    })
    for name, actual in versions.items():
        if actual != version:
            raise ValueError(f"{tag}: {name} has version {actual}")


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def variant_assets(directory: Path, variant: str) -> list[Path]:
    stem = STEMS[variant]
    archive = directory / f"{stem}.zip"
    parts = sorted(directory.glob(f"{stem}.zip.*"))
    merge = directory / f"{stem}-merge.bat"
    if archive.exists():
        if parts or merge.exists():
            raise ValueError(f"Mixed archive and split volumes: {stem}")
        files = [archive]
    else:
        numbers = [p.name.removeprefix(f"{stem}.zip.") for p in parts]
        if len(parts) < 2 or numbers != [f"{n:03d}" for n in range(1, len(parts) + 1)]:
            raise ValueError(f"Missing or non-contiguous volumes: {stem}")
        sources = set(re.findall(rf'"({re.escape(stem)}\.zip\.\d+)"', merge.read_text(encoding="ascii")))
        if sources != {p.name for p in parts}:
            raise ValueError(f"Merge script does not cover all volumes: {stem}")
        files = [*parts, merge]
    for path in files:
        if not path.is_file() or not 0 < path.stat().st_size < 2_000_000_000:
            raise ValueError(f"Empty or oversized release asset: {path.name}")
    return files


def check_assets(directory: Path, variant: str, *, write: bool = False) -> list[Path]:
    variants = tuple(STEMS) if variant == "both" else (variant,)
    result = []
    for item in variants:
        files = variant_assets(directory, item)
        if write:
            check_archive(files)
        manifest = directory / f"SHA256SUMS-{item}.txt"
        text = "".join(f"{digest(path)}  {path.name}\n" for path in files)
        if write:
            manifest.write_text(text, encoding="ascii", newline="\n")
        elif manifest.read_text(encoding="ascii") != text:
            raise ValueError(f"Checksum mismatch: {manifest.name}")
        result.extend([*files, manifest])
    if variant == "both" and {p.name for p in directory.iterdir()} != {p.name for p in result}:
        raise ValueError("Unexpected files in release assets")
    return result


def check_archive(files: list[Path]) -> None:
    archives = [p for p in files if p.suffix != ".bat"]

    def inspect(stream):
        with zipfile.ZipFile(stream) as archive:
            required = {f"funasr-subtitle/{name}" for name in (
                "funasr-subtitle.exe", "README.txt", "ffmpeg/ffmpeg.exe", "ffmpeg/ffprobe.exe",
            )}
            if not required <= set(archive.namelist()) or archive.testzip() is not None:
                raise ValueError("Portable archive is incomplete or corrupt")

    if len(archives) == 1:
        inspect(archives[0])
    else:
        # A temporary merged copy also validates that the uploaded volumes form one ZIP.
        with tempfile.TemporaryFile() as merged:
            for path in archives:
                with path.open("rb") as part:
                    shutil.copyfileobj(part, merged, length=4 * 1024 * 1024)
            merged.seek(0)
            inspect(merged)


def gh(*args: str, missing_ok: bool = False):
    result = subprocess.run(["gh", *args], check=False, capture_output=True, text=True, encoding="utf-8")
    if result.returncode:
        if missing_ok and ("HTTP 404" in result.stderr or "release not found" in result.stderr.lower()):
            return None
        raise RuntimeError(f"gh {' '.join(args[:2])} failed: {result.stderr}")
    return result.stdout


def check_uploaded(files: list[Path], assets: list[dict]) -> None:
    if {a["name"] for a in assets} != {p.name for p in files}:
        raise ValueError("Uploaded release asset list is incomplete or unexpected")
    by_name = {p.name: p for p in files}
    for asset in assets:
        path = by_name[asset["name"]]
        if (asset["state"] != "uploaded" or asset["size"] != path.stat().st_size
                or asset.get("digest") != f"sha256:{digest(path)}"):
            raise ValueError(f"Uploaded asset verification failed: {path.name}")


def publish(root: Path, directory: Path, tag: str, sha: str) -> None:
    check_version(root, tag)
    files = check_assets(directory, "both")
    repo = os.environ["GH_REPO"]
    if not re.fullmatch(r"[\w.-]+/[\w.-]+", repo):
        raise ValueError("Invalid GH_REPO")
    marker = f"<!-- funasr-subtitle-ci:{sha} -->"
    endpoint = f"repos/{repo}/releases"
    query = f'.[] | select(.tag_name == "{tag}")'

    def check_tag():
        remote = json.loads(gh("api", f"repos/{repo}/commits/{tag}"))
        if remote["sha"] != sha:
            raise ValueError("Release tag moved away from the tested commit")

    check_tag()
    raw = gh("api", endpoint, "--paginate", "--jq", query)
    release = json.loads(raw) if raw else None
    if release and not release["draft"]:
        check_uploaded(files, release["assets"])
        print(f"{tag} is already published with these exact assets")
        return
    if release:
        if marker not in (release.get("body") or ""):
            raise ValueError("Refusing to replace a draft not created by this CI commit")
        if {a["name"] for a in release["assets"]} - {p.name for p in files}:
            raise ValueError("Existing draft contains unexpected assets")
    else:
        gh("release", "create", tag, "--verify-tag", "--target", sha, "--draft",
           "--title", tag, "--generate-notes", "--notes", marker)
    gh("release", "upload", tag, *(str(p) for p in files), "--clobber")
    release = json.loads(gh("api", endpoint, "--paginate", "--jq", query))
    check_uploaded(files, release["assets"])
    check_tag()
    gh("release", "edit", tag, "--draft=false", "--verify-tag")
    print(f"Published {tag} with {len(files)} verified assets")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("version", "assets", "publish"))
    parser.add_argument("--tag", default="")
    parser.add_argument("--sha", default="")
    parser.add_argument("--directory", type=Path, default=ROOT / "packaging/out")
    parser.add_argument("--variant", choices=("onnx", "full", "both"), default="both")
    parser.add_argument("--write-checksums", action="store_true")
    args = parser.parse_args()
    if args.command == "version":
        check_version(ROOT, args.tag)
    elif args.command == "assets":
        check_assets(args.directory, args.variant, write=args.write_checksums)
    else:
        if not re.fullmatch(r"[0-9a-f]{40}", args.sha):
            parser.error("publish requires a full commit SHA")
        publish(ROOT, args.directory, args.tag, args.sha)
