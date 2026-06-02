"""REST + WebSocket 路由（§7）。"""
from __future__ import annotations

import asyncio
import shutil
import threading
import uuid
from pathlib import Path

from fastapi import APIRouter, File, Form, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, Response

from .. import __version__
from ..config import MAX_UPLOAD_MB, jobs_dir
from ..models import DEFAULT_MODEL_ID, ModelManager, get_model
from ..models.registry import ASR_MODELS
from ..schemas import JobOptions
from ..subtitle import EXPORT_FORMATS, export_segments

router = APIRouter(prefix="/api")


def _err(code: str, message: str, status: int = 400) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message}})


# ---- health ----
@router.get("/health")
async def health() -> dict:
    return {"status": "ok", "version": __version__}


# ---- models ----
@router.get("/models")
async def list_models(request: Request) -> dict:
    from ..engine import backend, compute_label
    mgr: ModelManager = request.app.state.models
    # 说话人分离仅完整版(full) + Paraformer 系（能出 sentence_info）支持
    diar_backend = backend() == "full"
    out = []
    for spec in ASR_MODELS:
        out.append({
            "id": spec.id, "name": spec.name, "task": spec.task,
            "size_mb": spec.size_mb, "downloaded": mgr.is_downloaded(spec.id),
            "diarization": diar_backend and spec.engine in ("paraformer", "contextual"),
        })
    return {"models": out, "default_id": DEFAULT_MODEL_ID,
            "compute": compute_label()}


@router.post("/models/{model_id:path}/download")
async def download_model(model_id: str, request: Request):
    try:
        get_model(model_id)
    except KeyError:
        return _err("unknown_model", f"no such model: {model_id}", 404)

    mgr: ModelManager = request.app.state.models
    jobs = request.app.state.jobs
    broker = jobs.broker
    jobs.model_status[model_id] = {"type": "downloading"}  # 进行中：迟到订阅者不会被误判终态

    def _run() -> None:
        def progress(pct: float, got_mb: float, total_mb: float) -> None:
            broker.publish(f"model:{model_id}", {
                "type": "download", "percent": pct,
                "downloaded_mb": got_mb, "total_mb": total_mb,
            })
        try:
            mgr.download(model_id, progress=progress)
            ev = {"type": "done"}
        except Exception as e:  # noqa: BLE001
            ev = {"type": "error", "code": "download_failed", "message": str(e)}
        jobs.model_status[model_id] = ev  # 记录终态，供 WS 迟到订阅者补发
        broker.publish(f"model:{model_id}", ev)

    threading.Thread(target=_run, daemon=True).start()
    return {"ok": True}


# ---- jobs ----
@router.post("/jobs")
async def create_job(
    request: Request,
    file: UploadFile = File(...),
    model_id: str = Form(""),
    language: str = Form("auto"),
    diarization: str = Form("false"),
    hotwords: str = Form(""),
    use_gpu: str = Form("false"),
    max_chars: int = Form(30),
    punctuation: str = Form("auto"),
):
    mgr = request.app.state.jobs
    # 落盘上传文件到 jobs/<uuid>/<filename>
    safe_name = Path(file.filename or "input").name
    dest_dir = jobs_dir() / uuid.uuid4().hex[:12]
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / safe_name
    limit = MAX_UPLOAD_MB * 1024 * 1024 if MAX_UPLOAD_MB > 0 else 0
    written = 0
    too_large = False
    try:
        with dest.open("wb") as f:
            while chunk := await file.read(1024 * 1024):
                written += len(chunk)
                if limit and written > limit:
                    too_large = True
                    break
                f.write(chunk)
    except Exception:  # noqa: BLE001  写盘失败/客户端中断 -> 清理半成品，不留垃圾
        shutil.rmtree(dest_dir, ignore_errors=True)
        return _err("upload_failed", "上传失败，请重试", 400)
    if too_large:
        shutil.rmtree(dest_dir, ignore_errors=True)
        return _err("file_too_large", f"文件超过上限 {MAX_UPLOAD_MB} MB", 413)

    options = JobOptions(
        model_id=model_id or DEFAULT_MODEL_ID,
        language=language if language in {"auto", "zh", "yue", "en", "ja", "ko"} else "auto",
        diarization=str(diarization).lower() in {"true", "1", "yes", "on"},
        hotwords=hotwords or "",
        use_gpu=str(use_gpu).lower() in {"true", "1", "yes", "on"},
        max_chars=max(0, min(100, int(max_chars))),
        punctuation=punctuation if punctuation in {"auto", "on", "off"} else "auto",
    )
    job = mgr.create(safe_name, dest, options)
    return {"job_id": job.id}


@router.get("/jobs/{job_id}")
async def get_job(job_id: str, request: Request):
    job = request.app.state.jobs.get(job_id)
    if not job:
        return _err("not_found", f"no such job: {job_id}", 404)
    return job.model_dump(mode="json")


@router.get("/jobs/{job_id}/export")
async def export_job(job_id: str, request: Request, format: str = "srt"):
    job = request.app.state.jobs.get(job_id)
    if not job:
        return _err("not_found", f"no such job: {job_id}", 404)
    fmt = format.lower()
    if fmt not in EXPORT_FORMATS:
        return _err("bad_format", f"unsupported format: {format}", 400)
    body = export_segments(job.segments, fmt)
    mime, ext = EXPORT_FORMATS[fmt]
    stem = Path(job.filename).stem or "subtitle"
    headers = {"Content-Disposition": f'attachment; filename="{stem}.{ext}"'}
    return Response(content=body, media_type=mime, headers=headers)


@router.post("/jobs/{job_id}/cancel")
async def cancel_job(job_id: str, request: Request):
    ok = request.app.state.jobs.cancel(job_id)
    if not ok:
        return _err("not_found", f"no such job: {job_id}", 404)
    return {"ok": True}


# ---- shutdown ----
@router.post("/shutdown")
async def shutdown(request: Request):
    request.app.state.request_shutdown()
    return {"ok": True}


# ---- WebSocket（注册到 app，不带 /api 前缀，匹配 spec /ws/...）----
def register_ws(app) -> None:
    @app.websocket("/ws/jobs/{job_id}")
    async def ws_job(ws: WebSocket, job_id: str):
        await ws.accept()
        mgr = ws.app.state.jobs
        broker = mgr.broker
        q = broker.subscribe(f"job:{job_id}")
        try:
            # 若任务已是终态，立即补发一次
            job = mgr.get(job_id)
            if job and job.status.value in {"done", "error"}:
                if job.status.value == "done":
                    await ws.send_json({"type": "done", "job": job.model_dump(mode="json")})
                else:
                    e = job.error
                    await ws.send_json({"type": "error",
                                        "code": e.code if e else "error",
                                        "message": e.message if e else ""})
            while True:
                event = await q.get()
                await ws.send_json(event)
                if event.get("type") in {"done", "error"}:
                    break
        except WebSocketDisconnect:
            pass
        finally:
            broker.unsubscribe(f"job:{job_id}", q)

    @app.websocket("/ws/models/{model_id:path}")
    async def ws_model(ws: WebSocket, model_id: str):
        await ws.accept()
        jobs = ws.app.state.jobs
        mgr = ws.app.state.models
        broker = jobs.broker
        q = broker.subscribe(f"model:{model_id}")
        try:
            # 以磁盘为准：已下载则立刻补发 done（覆盖「连上前就下完」「下载线程已死但文件已就位」
            # 以及避免对已被删除的模型补发过期 done）。
            try:
                already = mgr.is_downloaded(model_id)
            except Exception:  # noqa: BLE001  未知 model_id 等
                already = False
            if already:
                await ws.send_json({"type": "done"})
                return
            last = jobs.model_status.get(model_id)
            if last and last.get("type") == "error":  # 上次下载失败：补发错误
                await ws.send_json(last)
                return
            while True:
                event = await q.get()
                await ws.send_json(event)
                if event.get("type") in {"done", "error"}:
                    break
        except WebSocketDisconnect:
            pass
        finally:
            broker.unsubscribe(f"model:{model_id}", q)
