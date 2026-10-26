"""双击启动器：已有实例时直接打开旧页面，不再起第二个服务。"""
import json
import subprocess
import sys
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import ClassVar

import pytest

from app.main import _running_instance_url


class _HealthHandler(BaseHTTPRequestHandler):
    payload: ClassVar[dict] = {}

    def do_GET(self):
        body = json.dumps(self.payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass


def _serve(payload: dict) -> tuple[ThreadingHTTPServer, threading.Thread]:
    handler = type("Handler", (_HealthHandler,), {"payload": payload})
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def _stop(server: ThreadingHTTPServer, thread: threading.Thread) -> None:
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)


def test_detects_our_running_instance():
    server, thread = _serve({"status": "ok", "app": "funasr-subtitle"})
    port = server.server_address[1]
    try:
        assert _running_instance_url(port) == f"http://127.0.0.1:{port}"
    finally:
        _stop(server, thread)


def test_ignores_other_service_on_same_port():
    server, thread = _serve({"status": "ok", "app": "some-other-app"})
    try:
        assert _running_instance_url(server.server_address[1]) is None
    finally:
        _stop(server, thread)


def test_scan_clamps_at_valid_port_range():
    """高随机端口不能让扫描越过 65535 抛 OverflowError。"""
    assert _running_instance_url(65530) is None


@pytest.mark.skipif(sys.platform != "win32", reason="Windows 命名互斥锁")
def test_instance_lock_is_atomic_across_processes():
    """并发启动时，第二个进程必须拿不到锁。"""
    backend_dir = Path(__file__).resolve().parents[1]
    name = f"Local\\funasr-subtitle-test-{uuid.uuid4().hex}"
    holder_code = (
        "import sys, time\n"
        "from app.main import _acquire_instance_lock\n"
        "print('acquired' if _acquire_instance_lock(sys.argv[1]) else 'busy', flush=True)\n"
        "time.sleep(5)\n"
    )
    check_code = (
        "import sys, time\n"
        "from app.main import _acquire_instance_lock\n"
        "print('busy' if not _acquire_instance_lock(sys.argv[1]) else 'acquired', flush=True)\n"
        "time.sleep(0.1)\n"
    )
    holder = subprocess.Popen(
        [sys.executable, "-c", holder_code, name],
        cwd=backend_dir,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        assert holder.stdout is not None
        line = holder.stdout.readline().strip()
        assert line == "acquired", f"holder: {line!r}"

        result = subprocess.run(
            [sys.executable, "-c", check_code, name],
            cwd=backend_dir,
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0
        assert result.stdout.strip() == "busy"
    finally:
        holder.terminate()
        holder.wait(timeout=5)
        if holder.stdout is not None:
            holder.stdout.close()
