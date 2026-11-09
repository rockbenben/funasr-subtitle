"""浏览器 PCM 分块转写；推理仍交给文件任务共用的单 worker。"""
from __future__ import annotations

import asyncio
import json

from fastapi import WebSocket, WebSocketDisconnect
from pydantic import ValidationError

from ..models import get_model
from ..models.registry import STREAMING_PARAFORMER
from ..schemas import JobOptions
from .routes import _model_ready


def register_live_ws(app) -> None:
    @app.websocket("/ws/live")
    async def ws_live(ws: WebSocket):
        await ws.accept()
        mgr = ws.app.state.jobs
        job = None
        pending = None
        flushed = False
        try:
            config = await asyncio.wait_for(ws.receive_json(), timeout=15)
            if not isinstance(config, dict) or config.get("source") not in {"microphone", "system"}:
                raise ValueError("请选择麦克风或电脑声音。")
            options = JobOptions.model_validate(config.get("options", {}))
            options.model_id = options.model_id or STREAMING_PARAFORMER.id
            if not get_model(options.model_id).streaming:
                raise ValueError("实时转写需要选择 Paraformer 实时版模型。")
            if not _model_ready(ws.app.state.models, options.model_id):
                raise ValueError("请先下载所选模型，再开始实时转写。")
            job = mgr.create_live(options, config["source"])
            await ws.send_json({"type": "created", "job": job.model_dump(mode="json")})
            pending = asyncio.wrap_future(mgr.submit_live(job.id, b""))
            await asyncio.shield(pending)
            await ws.send_json({"type": "ready"})
            while True:
                packet = await asyncio.wait_for(ws.receive(), timeout=60)
                if packet["type"] == "websocket.disconnect":
                    break
                pcm = packet.get("bytes")
                if pcm is not None:
                    # 上游官方在线模型的 chunk_size[1] * 960 = 9600 个采样点（600ms）。
                    if not pcm or len(pcm) % 2 or len(pcm) > 9600 * 2:
                        raise ValueError("录音数据不完整，请停止后重新开始。")
                    pending = asyncio.wrap_future(mgr.submit_live(job.id, pcm))
                    update = await asyncio.shield(pending)
                    await ws.send_json({"type": "chunk", **update})
                elif json.loads(packet.get("text") or "{}") == {"type": "stop"}:
                    pending = asyncio.wrap_future(mgr.submit_live(job.id, b"", final=True))
                    await asyncio.shield(pending)
                    flushed = True
                    mgr.finish_live(job.id)
                    await ws.send_json({"type": "done", "job": job.model_dump(mode="json")})
                    break
                else:
                    raise ValueError("录音请求无效，请停止后重新开始。")
        except WebSocketDisconnect:
            pass
        except (ValueError, KeyError, TimeoutError) as exc:
            message = "录音请求无效或连接超时，请重试。"
            if job is not None and job.error:
                message = job.error.message
            elif isinstance(exc, ValueError) and not isinstance(exc, (ValidationError, json.JSONDecodeError)):
                message = str(exc)
            await ws.send_json({"type": "error", "message": message, "detail": str(exc)})
        except Exception as exc:  # noqa: BLE001
            # 客户端断开也会走这里；已转写内容留在 Job 中，刷新可恢复、导出。
            if ws.client_state.name == "CONNECTED":
                try:
                    await ws.send_json({"type": "error", "message": "实时转写中断了，已显示的字幕仍可导出。",
                                        "detail": str(exc)})
                except (RuntimeError, WebSocketDisconnect, OSError):
                    pass
        finally:
            if job is not None and not flushed and mgr.is_live(job.id):
                # 断线时 ASGI 协程可能立即被取消；收尾也入同一队列，由 worker 完成后释放会话。
                mgr.submit_live(job.id, b"", final=True)
            try:
                await ws.close()
            except (RuntimeError, WebSocketDisconnect, OSError):
                pass
