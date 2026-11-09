"""实时转写合同：使用假引擎，CI 不需要模型、声卡或 torch。"""
import threading
import time
from itertools import pairwise

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.models.registry import STREAMING_PARAFORMER
from app.schemas import Job, JobStatus


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("FUNASR_SUBTITLE_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("MODELSCOPE_CACHE", raising=False)
    monkeypatch.setattr("app.api.live._model_ready", lambda *_: True)
    app = create_app()

    class Engine:
        def load(self):
            assert threading.current_thread().name == "funasr-subtitle-worker"

        def feed(self, wav, *, final=False):
            assert threading.current_thread().name == "funasr-subtitle-worker"
            assert wav.dtype == np.float32
            if final:
                return "尾字" if len(self.__dict__) else ""
            if np.any(wav):
                self.spoke = True
                return "课堂内容"
            return ""

    monkeypatch.setattr("app.jobs.queue.LiveEngine", Engine)
    with TestClient(app) as client:
        yield client


def start(ws, source="microphone"):
    ws.send_json({"source": source, "options": {"model_id": STREAMING_PARAFORMER.id, "diarization": True}})
    job = ws.receive_json()
    assert job["type"] == "created"
    assert ws.receive_json() == {"type": "ready"}
    return job["job"]["id"]


def wait_finished(client, job_id):
    for _ in range(100):
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] in {"done", "error"}:
            return job
        time.sleep(0.01)
    pytest.fail("录音连接结束后未释放任务")


@pytest.mark.parametrize("source", ["microphone", "system"])
def test_live_chunks_offsets_tail_and_export(client, source):
    with client.websocket_connect("/ws/live") as ws:
        job_id = start(ws, source)
        ws.send_bytes(bytes(9600 * 2))
        assert ws.receive_json() == {"type": "chunk", "replace_from": 0, "segments": []}
        ws.send_bytes(np.full(9600, 1600, dtype="<i2").tobytes())
        result = ws.receive_json()
        assert result["segments"][0]["start_ms"] == 600
        assert result["segments"][0]["words"] is None
        # 草稿要替换而非重复追加；停止时短尾块与上游 final flush 都不能丢。
        ws.send_bytes(np.full(4000, 1600, dtype="<i2").tobytes())
        tail = ws.receive_json()["segments"][0]
        assert tail["text"] == "课堂内容课堂内容"
        assert tail["end_ms"] == 1450
        ws.send_json({"type": "stop"})
        done = ws.receive_json()
        assert done["type"] == "done"
        assert done["job"]["source"] == source
        assert done["job"]["options"]["diarization"] is False
        assert done["job"]["segments"][0]["text"] == "课堂内容课堂内容尾字"
    assert not client.app.state.jobs.live_active
    for fmt in ("srt", "vtt", "txt", "json"):
        response = client.get(f"/api/jobs/{job_id}/export?format={fmt}")
        assert response.status_code == 200 and "课堂" in response.text


def test_live_excludes_other_jobs_and_releases_on_disconnect(client):
    with client.websocket_connect("/ws/live") as ws:
        job_id = start(ws)
        with client.websocket_connect("/ws/live") as other:
            other.send_json({"source": "system"})
            assert other.receive_json()["type"] == "error"
        response = client.post("/api/jobs", files={"file": ("a.wav", b"data")})
        assert response.status_code == 409
        ws.send_bytes(np.full(9600, 1000, dtype="<i2").tobytes())
        assert ws.receive_json()["segments"]
    job = wait_finished(client, job_id)
    assert job["segments"]
    assert not client.app.state.jobs.live_active
    with client.websocket_connect("/ws/live") as ws:
        start(ws)
        ws.send_json({"type": "stop"})
        assert ws.receive_json()["type"] == "done"


def test_reject_live_while_file_queued(client):
    client.app.state.jobs.jobs["file"] = Job(id="file", filename="a.wav", status=JobStatus.queued)
    with client.websocket_connect("/ws/live") as ws:
        ws.send_json({"source": "microphone"})
        assert ws.receive_json()["type"] == "error"
    assert not client.app.state.jobs.live_active


@pytest.mark.parametrize("payload", [b"x", b"", bytes(19202)], ids=["odd", "empty", "oversized"])
def test_invalid_pcm_releases_session(client, payload):
    with client.websocket_connect("/ws/live") as ws:
        job_id = start(ws)
        ws.send_bytes(payload)
        assert ws.receive_json()["type"] == "error"
    assert wait_finished(client, job_id)["segments"] == []
    assert not client.app.state.jobs.live_active


@pytest.mark.parametrize("config", [None, [], {"source": "invalid"},
                                   {"source": "system", "options": {"pause_ms": -1}},
                                   {"source": "system", "options": {"model_id": "no/model"}}])
def test_invalid_start_never_creates_job(client, config):
    with client.websocket_connect("/ws/live") as ws:
        ws.send_json(config)
        assert ws.receive_json()["type"] == "error"
    assert not client.app.state.jobs.jobs


def test_requires_downloaded_model(client, monkeypatch):
    monkeypatch.setattr("app.api.live._model_ready", lambda *_: False)
    with client.websocket_connect("/ws/live") as ws:
        ws.send_json({"source": "system"})
        assert "下载" in ws.receive_json()["message"]
    assert not client.app.state.jobs.jobs


def test_live_rejects_offline_model_and_file_rejects_online_model(client):
    from app.models import DEFAULT_MODEL_ID

    with client.websocket_connect("/ws/live") as ws:
        ws.send_json({"source": "microphone", "options": {"model_id": DEFAULT_MODEL_ID}})
        assert "实时版" in ws.receive_json()["message"]
    response = client.post("/api/jobs", files={"file": ("a.wav", b"data")},
                           data={"model_id": STREAMING_PARAFORMER.id})
    assert response.json()["error"]["code"] == "live_only"


def test_draft_replacement_keeps_long_transcript_without_duplicates(client):
    from app.subtitle.segmentation import _visible_len

    with client.websocket_connect("/ws/live") as ws:
        job_id = start(ws)
        displayed = []
        for _ in range(30):
            ws.send_bytes(np.full(9600, 1600, dtype="<i2").tobytes())
            update = ws.receive_json()
            displayed[update["replace_from"]:] = update["segments"]
        assert "".join(s["text"] for s in displayed) == "课堂内容" * 30
        ws.send_json({"type": "stop"})
        segments = ws.receive_json()["job"]["segments"]
        assert "".join(s["text"] for s in segments) == "课堂内容" * 30 + "尾字"
        assert all(_visible_len(s["text"]) <= 30 and s["words"] is None for s in segments)
        assert all(a["end_ms"] <= b["start_ms"] for a, b in pairwise(segments))
    assert wait_finished(client, job_id)["status"] == "done"


def test_inference_failure_keeps_previous_subtitles(client, monkeypatch):
    with client.websocket_connect("/ws/live") as ws:
        job_id = start(ws)
        ws.send_bytes(np.full(9600, 1600, dtype="<i2").tobytes())
        assert ws.receive_json()["segments"]

        def fail(*_, **kwargs):
            raise RuntimeError("inference failure")

        session = client.app.state.jobs._live_sessions[job_id]
        monkeypatch.setattr(session.engine, "feed", fail)
        ws.send_bytes(np.full(9600, 1600, dtype="<i2").tobytes())
        error = ws.receive_json()
        assert error["type"] == "error" and error["detail"] == "inference failure"
    assert wait_finished(client, job_id)["status"] == "error"
    assert "课堂内容" in client.get(f"/api/jobs/{job_id}/export?format=txt").text
    assert not client.app.state.jobs.live_active
