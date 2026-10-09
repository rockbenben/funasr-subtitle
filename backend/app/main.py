"""FastAPI 实例 + 路由挂载 + StaticFiles + 启动逻辑（端口/浏览器/托盘）（§4）。

开发：uvicorn app.main:app --reload --port 8765
发布：冻结出的 funasr-subtitle.exe 调用 run()（找空闲端口 -> 起服务 -> 开浏览器 -> 托盘）。
"""
from __future__ import annotations

import asyncio
import contextlib
import os
import socket
import threading
import webbrowser
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from .api.routes import register_ws, router
from .config import DEFAULT_PORT, bundle_dir, is_frozen
from .jobs import JobManager
from .models import ModelManager


def _frontend_dist() -> Path | None:
    """前端静态产物目录：发布期在包内，开发期在 ../frontend/dist。"""
    bundle = [bundle_dir() / "frontend_dist", bundle_dir() / "_internal" / "frontend_dist"]
    dev = [Path(__file__).resolve().parents[2] / "frontend" / "dist"]
    # 开发态必须先看 ../frontend/dist：backend/frontend_dist 是上一次打包留下的快照，
    # 它排在前面的话，uvicorn 会静默服务几周前的界面，而新构建看着像没生效。
    candidates = dev + bundle if not is_frozen() else bundle + dev
    for c in candidates:
        if c.is_dir() and (c / "index.html").exists():
            return c
    return None


@asynccontextmanager
async def _lifespan(app: FastAPI):
    app.state.jobs.start(asyncio.get_running_loop())
    try:
        yield
    finally:
        with contextlib.suppress(Exception):
            app.state.jobs.stop()


def create_app() -> FastAPI:
    app = FastAPI(title="funasr-subtitle", version=__version__, lifespan=_lifespan)

    app.state.jobs = JobManager()
    app.state.models = ModelManager()
    app.state._server = None
    app.state._shutdown = threading.Event()

    def request_shutdown() -> None:
        app.state._shutdown.set()
        with contextlib.suppress(Exception):
            app.state.jobs.stop()
            app.state.jobs.cleanup()
        server = app.state._server
        if server is not None:
            server.should_exit = True
        else:
            # 开发模式（uvicorn --reload 无 server 句柄）：延迟硬退出
            threading.Timer(0.5, lambda: os._exit(0)).start()

    app.state.request_shutdown = request_shutdown

    # 统一错误响应 {error:{code,message[,detail]}}（§7）：message 给人看，detail 放原文。
    @app.exception_handler(RequestValidationError)
    async def _validation(_req: Request, exc: RequestValidationError):
        return JSONResponse(status_code=422,
                            content={"error": {"code": "validation_error",
                                               "message": "请求内容不完整，请重试。",
                                               "detail": str(exc.errors())}})

    @app.exception_handler(Exception)
    async def _unhandled(_req: Request, exc: Exception):
        return JSONResponse(status_code=500,
                            content={"error": {"code": "internal_error",
                                               "message": "后台出错了，请重试；还不行就重启程序。",
                                               "detail": str(exc)}})

    # 路由
    app.include_router(router)
    register_ws(app)

    # 前端静态资源（放在最后，作为 SPA 兜底）
    dist = _frontend_dist()
    if dist is not None:
        app.mount("/", StaticFiles(directory=str(dist), html=True), name="spa")

    return app


app = create_app()


# ---- 便携启动器（frozen funasr-subtitle.exe 入口）----

def _find_free_port(preferred: int = DEFAULT_PORT) -> int:
    for port in range(preferred, preferred + 50):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    # 兜底：让系统分配
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _start_tray(url: str, shutdown) -> None:  # noqa: ANN001
    """系统托盘（可选，§4）。缺 pystray/pillow 时静默跳过。"""
    try:
        import pystray
        from PIL import Image, ImageDraw
    except Exception:  # noqa: BLE001  # 缺 pystray/pillow 时静默跳过托盘（可选功能）
        return

    img = Image.new("RGB", (64, 64), (32, 32, 40))
    d = ImageDraw.Draw(img)
    d.ellipse((14, 14, 50, 50), fill=(90, 160, 255))

    def _open(icon, item):  # noqa: ANN001
        webbrowser.open(url)

    def _quit(icon, item):  # noqa: ANN001
        icon.stop()
        shutdown()

    icon = pystray.Icon("funasr-subtitle", img, "funasr-subtitle",
                        menu=pystray.Menu(
                            pystray.MenuItem("打开 funasr-subtitle", _open, default=True),
                            pystray.MenuItem("退出", _quit)))
    threading.Thread(target=icon.run, daemon=True).start()


def run() -> None:
    import uvicorn

    port = _find_free_port()
    url = f"http://127.0.0.1:{port}"

    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    app.state._server = server

    _start_tray(url, app.state.request_shutdown)

    # 服务起来后开浏览器
    def _open_browser() -> None:
        import time
        for _ in range(40):
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.25):
                    break
            except OSError:
                time.sleep(0.1)
        webbrowser.open(url)

    threading.Thread(target=_open_browser, daemon=True).start()
    server.run()


if __name__ == "__main__":
    run()
