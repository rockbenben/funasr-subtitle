"""API 冒烟测试：不触发真实模型推理，只验证路由/契约形状。"""
import tempfile

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.schemas import Job, JobStatus, Segment


@pytest.fixture()
def client(monkeypatch):
    # 用临时数据目录隔离，避免污染真实 %LOCALAPPDATA%\funasr-subtitle
    monkeypatch.setenv("FUNASR_SUBTITLE_DATA_DIR", tempfile.mkdtemp(prefix="fs_test_"))
    with TestClient(create_app()) as c:
        yield c


def _inject_done_job(client, filename: str) -> None:
    job = Job(
        id="cjk",
        filename=filename,
        status=JobStatus.done,
        segments=[Segment(start_ms=0, end_ms=1000, text="你好")],
    )
    client.app.state.jobs.jobs[job.id] = job


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok" and "version" in body
    assert body["app"] == "funasr-subtitle"


def test_models_contract(client):
    r = client.get("/api/models")
    assert r.status_code == 200
    body = r.json()
    assert "models" in body and "default_id" in body
    assert any(m["id"] == body["default_id"] for m in body["models"])
    for m in body["models"]:
        assert {"id", "name", "task", "size_mb", "downloaded"} <= set(m)


def test_job_not_found_error_shape(client):
    r = client.get("/api/jobs/nope")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "not_found"


def test_unknown_model_download(client):
    r = client.post("/api/models/does%2Fnotexist/download")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "unknown_model"


def test_export_bad_format(client):
    # 任务不存在时优先 404；此处只验证格式校验路径存在
    r = client.get("/api/jobs/whatever/export", params={"format": "xml"})
    assert r.status_code == 404  # 任务不存在


def test_export_non_ascii_filename_uses_rfc5987(client):
    """中文源文件名必须能导出；HTTP 头只能放 ASCII，文件名走 RFC 5987。"""
    _inject_done_job(client, "会议录音 2026-10-26.wav")
    r = client.get("/api/jobs/cjk/export", params={"format": "srt"})
    assert r.status_code == 200
    disposition = r.headers["content-disposition"]
    assert "filename*=UTF-8''" in disposition
    assert "%E4%BC%9A%E8%AE%AE%E5%BD%95%E9%9F%B3" in disposition
    assert "你好" in r.text
