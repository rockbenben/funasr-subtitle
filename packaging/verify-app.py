"""Smoke-test the frozen app with isolated data and no developer tools on PATH."""
import argparse
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.error import URLError
from urllib.parse import quote
from urllib.request import Request, urlopen


def check_inference(base: str, fixture: Path, model_id: str) -> None:
    with urlopen(Request(base + f"/api/models/{quote(model_id, safe='')}/download", method="POST"),
                 timeout=30):
        pass
    deadline = time.monotonic() + 900
    while True:
        with urlopen(base + "/api/models", timeout=45) as response:
            models = json.load(response)
        if next(m for m in models["models"] if m["id"] == model_id)["downloaded"]:
            break
        if time.monotonic() >= deadline:
            raise RuntimeError("Smoke-test model download timed out")
        time.sleep(2)
    boundary = "funasr-smoke-upload"
    body = (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="sample_zh.wav"\r\n'
            'Content-Type: audio/wav\r\n\r\n').encode() + fixture.read_bytes()
    body += f"\r\n--{boundary}--\r\n".encode()
    request = Request(base + "/api/jobs", data=body,
                      headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    with urlopen(request, timeout=30) as response:
        job_id = json.load(response)["job_id"]
    deadline = time.monotonic() + 300
    while True:
        with urlopen(base + f"/api/jobs/{job_id}", timeout=30) as response:
            job = json.load(response)
        if job["status"] == "done":
            break
        if job["status"] in ("error", "cancelled") or time.monotonic() >= deadline:
            raise RuntimeError(f"Frozen transcription failed: {job}")
        time.sleep(1)
    text = "".join(segment["text"] for segment in job["segments"])
    assert "公园" in text or "测试" in text, text
    for fmt in ("srt", "vtt"):
        with urlopen(base + f"/api/jobs/{job_id}/export?format={fmt}", timeout=10) as response:
            assert b"-->" in response.read(), f"Empty {fmt} export"
    print("Frozen transcription + SRT/VTT export OK")


def verify(app: Path, variant: str, *, inference: bool = False) -> None:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix="funasr-frozen-") as data:
        env = {**os.environ, "PATH": "", "FUNASR_SUBTITLE_PORT": str(port),
               "FUNASR_SUBTITLE_DATA_DIR": data, "MODELSCOPE_CACHE": str(Path(data) / "models")}
        # Verify the baked marker, rather than accidentally inheriting a dev override.
        env.pop("FUNASR_SUBTITLE_BACKEND", None)
        # A windowless no-op browser keeps the smoke test off the user's desktop.
        pythonw = Path(sys.executable).with_name("pythonw.exe")
        if pythonw.exists():
            env["BROWSER"] = f'"{pythonw}" -c pass %s'
        proc = subprocess.Popen([str(app.resolve())], env=env, creationflags=subprocess.CREATE_NO_WINDOW)
        base = f"http://127.0.0.1:{port}"
        try:
            deadline = time.monotonic() + 90
            while True:
                if proc.poll() is not None:
                    raise RuntimeError(f"Frozen app exited early: {proc.returncode}")
                try:
                    with urlopen(base + "/api/health", timeout=2) as response:
                        health = json.load(response)
                    break
                except (URLError, TimeoutError):
                    if time.monotonic() >= deadline:
                        raise RuntimeError("Frozen app did not become ready") from None
                    time.sleep(0.2)
            assert health["status"] == "ok" and health["app"] == "funasr-subtitle", health
            with urlopen(base + "/api/models", timeout=45) as response:
                models = json.load(response)
            default = next(m for m in models["models"] if m["id"] == models["default_id"])
            assert (default["size_mb"] > 500) == (variant == "full"), default
            assert all(not m["downloaded"] for m in models["models"]), models
            with urlopen(base, timeout=5) as response:
                assert b"<html" in response.read().lower(), "Bundled frontend missing"
            if inference:
                fixture = Path(__file__).resolve().parents[1] / "backend/tests/fixtures/sample_zh.wav"
                check_inference(base, fixture, models["default_id"])
            with urlopen(Request(base + "/api/shutdown", method="POST"), timeout=5):
                pass
            proc.wait(timeout=15)
            assert proc.returncode == 0, proc.returncode
            print(f"Frozen app OK ({variant}): {models['compute']}")
        except Exception:
            log = Path(data) / "startup.log"
            if log.exists():
                print(log.read_text(encoding="utf-8", errors="replace"))
            raise
        finally:
            if proc.poll() is None:
                proc.terminate()
                proc.wait(timeout=15)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--app", type=Path, required=True)
    parser.add_argument("--variant", choices=("onnx", "full"), required=True)
    parser.add_argument("--inference", action="store_true")
    args = parser.parse_args()
    verify(args.app, args.variant, inference=args.inference)
