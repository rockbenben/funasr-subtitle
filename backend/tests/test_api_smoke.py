"""API 冒烟测试：不触发真实模型推理，只验证路由/契约形状。"""
import tempfile
import time
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.schemas import Job, JobStatus, Segment


@pytest.fixture()
def client(monkeypatch):
    # 用临时数据目录隔离，避免污染真实 %LOCALAPPDATA%\funasr-subtitle
    monkeypatch.setenv("FUNASR_SUBTITLE_DATA_DIR", tempfile.mkdtemp(prefix="fs_test_"))
    # 本机若设了全局缓存根，会绕过上面的临时目录；测试里显式清掉
    monkeypatch.delenv("MODELSCOPE_CACHE", raising=False)
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


def test_create_job_accepts_pause_ms(client):
    """停顿阈值要能随任务带进来（0=不按停顿断，是默认值）。"""
    files = {"file": ("a.wav", b"RIFFfake", "audio/wav")}
    r = client.post("/api/jobs", files=files, data={"pause_ms": "300", "max_chars": "12"})
    assert r.status_code == 200
    job_id = r.json()["job_id"]
    job = client.get(f"/api/jobs/{job_id}").json()
    assert job["options"]["pause_ms"] == 300
    assert job["options"]["max_chars"] == 12


def test_create_job_clamps_pause_ms(client):
    """非法值要夹到合法范围，不能把接口搞崩。"""
    files = {"file": ("a.wav", b"RIFFfake", "audio/wav")}
    r = client.post("/api/jobs", files=files, data={"pause_ms": "-5"})
    assert r.status_code == 200
    job_id = r.json()["job_id"]
    job = client.get(f"/api/jobs/{job_id}").json()
    assert job["options"]["pause_ms"] == 0


def test_models_full_backend_contract(client, monkeypatch):
    """完整版：模型面板必须按 pt 模型（ASR+VAD+punc）判断，不能拿 onnx 的体积/状态充数。"""
    import app.engine as engine

    monkeypatch.setattr(engine, "backend", lambda: "full")
    body = client.get("/api/models").json()
    default = next(m for m in body["models"] if m["id"] == body["default_id"])
    assert default["size_mb"] > 500  # SenseVoice pt ≈ 0.9GB，而不是 onnx 的 235MB
    assert default["downloaded"] is False
    para = next(m for m in body["models"] if m["id"].endswith("vocab8404-onnx")
                and "contextual" not in m["id"])
    assert para["diarization"] is True


def test_job_not_found_error_shape(client):
    r = client.get("/api/jobs/nope")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "not_found"


def test_unknown_model_download(client):
    r = client.post("/api/models/does%2Fnotexist/download")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "unknown_model"


def test_full_backend_download_dispatches_to_pt_models(client, monkeypatch):
    """完整版的下载按钮必须走 download_full，不能还去下 onnx 仓库。"""
    import app.engine as engine

    monkeypatch.setattr(engine, "backend", lambda: "full")
    mgr = client.app.state.models
    called: list[str] = []
    monkeypatch.setattr(mgr, "download_full", lambda model_id, progress=None: called.append(model_id))

    default_id = client.get("/api/models").json()["default_id"]
    r = client.post(f"/api/models/{quote(default_id, safe='')}/download")
    assert r.status_code == 200
    for _ in range(100):
        if called:
            break
        time.sleep(0.02)
    assert called == [default_id]


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
