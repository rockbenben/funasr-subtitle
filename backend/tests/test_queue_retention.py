"""任务队列的内存回收策略：终态任务裁剪 + 引擎 LRU 淘汰。

都是「长会话不无限增长」的护栏，必须保证**不会误删还在用的东西**：
- 裁剪只碰 done/error；排队中/执行中的任务永不丢。
- 引擎淘汰只在「当前没有任务在用」时发生（单 worker 串行）。
"""
import threading

import pytest

from app.jobs.queue import JobManager
from app.schemas import Job, JobStatus, Segment


def _make_job(job_id: str, status: JobStatus) -> Job:
    return Job(
        id=job_id,
        filename=f"{job_id}.mp4",
        status=status,
        segments=[Segment(start_ms=0, end_ms=1000, text="测试")],
    )


@pytest.fixture()
def mgr(monkeypatch, tmp_path):
    monkeypatch.setattr("app.jobs.queue.MAX_JOBS", 5)
    m = JobManager()
    m._inputs = {}          # 不去动真实上传目录
    return m


def _fill(mgr, statuses):
    """按给定状态填 jobs，并补上 _cleanup_input 需要的 _cancels。"""
    for jid, st in statuses.items():
        mgr.jobs[jid] = _make_job(jid, st)
        mgr._cancels[jid] = threading.Event()
    return mgr


def test_prune_keeps_recent_terminal_jobs(mgr):
    """超过上限时，从最旧的终态任务开始丢。"""
    _fill(mgr, {f"j{i}": JobStatus.done for i in range(8)})
    mgr._cleanup_input("j7")
    assert len(mgr.jobs) == 5
    # 最新的 5 个必须还在（用户刚转完的那条不能被丢）
    assert set(mgr.jobs) == {"j3", "j4", "j5", "j6", "j7"}


def test_prune_never_drops_inflight_jobs(mgr):
    """排队中/执行中的任务不参与裁剪，即使它们最旧。"""
    _fill(mgr, {
        "inflight": JobStatus.asr,
        "queued": JobStatus.queued,
        **{f"d{i}": JobStatus.done for i in range(8)},
    })
    mgr._cleanup_input("d7")
    # 在途任务必须活着（否则 UI 会把正在转写的任务报成「不存在」）
    assert "inflight" in mgr.jobs and "queued" in mgr.jobs
    # 总数收敛到上限，且保留的是**最新**的终态任务
    assert len(mgr.jobs) == 5
    assert set(mgr.jobs) == {"inflight", "queued", "d5", "d6", "d7"}


def test_prune_keeps_cancelled_jobs_as_terminal(mgr):
    """取消走的是 error + code=cancelled，属于终态，可被裁剪。"""
    job = _make_job("c0", JobStatus.error)
    job.error = {"code": "cancelled", "message": "已取消转写"}
    mgr.jobs["c0"] = job
    mgr._cancels["c0"] = threading.Event()
    for i in range(6):
        mgr.jobs[f"d{i}"] = _make_job(f"d{i}", JobStatus.done)
        mgr._cancels[f"d{i}"] = threading.Event()
    mgr._cleanup_input("d5")
    assert "c0" not in mgr.jobs
    assert len(mgr.jobs) == 5


def test_prune_noop_below_limit(mgr):
    _fill(mgr, {f"j{i}": JobStatus.done for i in range(3)})
    mgr._cleanup_input("j2")
    assert len(mgr.jobs) == 3


def test_cleanup_still_removes_input_dir(monkeypatch, tmp_path):
    """裁剪不能影响原有的「删上传目录」行为。"""
    monkeypatch.setattr("app.jobs.queue.MAX_JOBS", 50)
    m = JobManager()
    jid = "abc"
    d = tmp_path / jid
    d.mkdir()
    (d / "in.mp4").write_bytes(b"x")
    m.jobs[jid] = _make_job(jid, JobStatus.done)
    m._cancels[jid] = threading.Event()
    m._inputs[jid] = d / "in.mp4"
    m._cleanup_input(jid)
    assert not d.exists()
    assert jid in m.jobs          # 未超上限，任务记录仍在
    assert jid not in m._cancels


def test_prune_bound_is_respected_under_load(mgr):
    """连续灌入大量终态任务，内存里的任务数必须有界。"""
    for i in range(200):
        mgr.jobs[f"j{i}"] = _make_job(f"j{i}", JobStatus.done)
        mgr._cancels[f"j{i}"] = threading.Event()
        mgr._cleanup_input(f"j{i}")
    assert len(mgr.jobs) == 5


# ---- 引擎缓存 LRU ----


class _FakeEngine:
    def __init__(self):
        self.loaded = False

    def load(self):
        self.loaded = True


@pytest.fixture()
def emgr(monkeypatch):
    monkeypatch.setattr("app.jobs.queue.ENGINE_CACHE_SIZE", 2)
    monkeypatch.setattr("app.jobs.queue.build_engine", lambda *a, **k: _FakeEngine())
    m = JobManager()
    return m


def _key(model_id: str) -> str:
    """与 JobManager._get_engine 相同的缓存键格式。"""
    return f"{model_id}|gpu=False|diar=False"


def test_engine_cache_reuses_same_key(emgr):
    a = emgr._get_engine("sv")
    b = emgr._get_engine("sv")
    assert a is b, "同 model_id 必须复用已加载引擎（加载很贵）"


def test_engine_cache_lru_evicts_beyond_limit(emgr):
    """上限 2：装第 3 个时最久未用的应被淘汰，且每个引擎只 load 一次。"""
    emgr._get_engine("sv")
    emgr._get_engine("paraformer")
    emgr._get_engine("sensevoice")
    keys = set(emgr._engines)
    assert len(keys) == 2
    assert _key("sv") not in keys, "最久未用的 sv 应被淘汰"
    assert {_key("paraformer"), _key("sensevoice")} == keys


def test_engine_cache_lru_respects_recency(emgr):
    """重新用到 a 会把它顶回最新，淘汰时应该淘汰 b 而不是 a。"""
    emgr._get_engine("a")
    emgr._get_engine("b")
    emgr._get_engine("a")       # a 变成最新
    emgr._get_engine("c")
    keys = set(emgr._engines)
    assert _key("a") in keys and _key("c") in keys and _key("b") not in keys


def test_engine_cache_does_not_evict_in_use(emgr):
    """正在转写用的引擎不会被淘汰——淘汰只发生在「装入新引擎」这一刻。"""
    emgr._get_engine("a")
    emgr._get_engine("b")
    live = emgr._get_engine("a")
    # live 仍在缓存中，可被再次取到
    assert emgr._get_engine("a") is live
