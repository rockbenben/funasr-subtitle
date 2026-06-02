"""任务队列 + 状态机 + 事件广播（§6B, §7, §8）。

- 单用户单机：内存存储 Job + 一个后台 worker 线程串行处理队列。
- ASR 是阻塞型 CPU 任务，放在 worker 线程里跑；通过 EventBroker 把进度事件
  跨线程投递到 asyncio 侧的 WebSocket。
- 引擎按 model_id 缓存复用（加载昂贵）。
"""
from __future__ import annotations

import asyncio
import queue as _queue
import shutil
import threading
import traceback
import uuid
from pathlib import Path
from typing import Optional

from ..config import jobs_dir
from ..engine import build_engine
from ..media import DecodeCancelled, DecodeError, decode_to_16k_mono
from ..schemas import Job, JobError, JobOptions, JobStatus, Segment


class EventBroker:
    """把后台线程产生的事件投递给 asyncio 侧的订阅者（每个 WS 一个 asyncio.Queue）。"""

    def __init__(self) -> None:
        self._subs: dict[str, list[asyncio.Queue]] = {}
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._lock = threading.Lock()

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def subscribe(self, key: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        with self._lock:
            self._subs.setdefault(key, []).append(q)
        return q

    def unsubscribe(self, key: str, q: asyncio.Queue) -> None:
        with self._lock:
            if key in self._subs and q in self._subs[key]:
                self._subs[key].remove(q)

    def publish(self, key: str, event: dict) -> None:
        """线程安全：可从 worker 线程调用。"""
        if self._loop is None:
            return
        with self._lock:
            subs = list(self._subs.get(key, []))
        for q in subs:
            try:
                self._loop.call_soon_threadsafe(q.put_nowait, event)
            except RuntimeError:
                pass  # loop 已关闭


class JobManager:
    def __init__(self) -> None:
        self.jobs: dict[str, Job] = {}
        # 每个模型最近一次下载的终态（done/error），供 WS 迟到订阅者补发
        self.model_status: dict[str, dict] = {}
        self.broker = EventBroker()
        self._q: _queue.Queue[str] = _queue.Queue()
        self._inputs: dict[str, Path] = {}
        self._cancels: dict[str, threading.Event] = {}
        self._engines: dict[str, object] = {}
        self._engine_lock = threading.Lock()
        self._worker: Optional[threading.Thread] = None
        self._stop = threading.Event()

    # ---- 生命周期 ----
    def start(self, loop: asyncio.AbstractEventLoop) -> None:
        self.broker.bind_loop(loop)
        if self._worker is None:
            self._worker = threading.Thread(target=self._run, daemon=True, name="funasr-subtitle-worker")
            self._worker.start()

    def stop(self) -> None:
        self._stop.set()
        # 取消所有在途任务，让当前 job 的解码/推理尽快中断。
        # 用快照遍历：worker 线程可能并发 pop _cancels（终态清理），避免「dict 遍历中改大小」。
        for ev in list(self._cancels.values()):
            ev.set()
        self._q.put("")  # 唤醒 worker
        w = self._worker
        if w is not None and w.is_alive():
            w.join(timeout=5.0)  # 关机前等 worker 退出，避免 cleanup 与读文件竞争

    # ---- 提交/查询/取消 ----
    def create(self, filename: str, input_path: Path, options: JobOptions) -> Job:
        job_id = uuid.uuid4().hex[:12]
        job = Job(id=job_id, filename=filename, status=JobStatus.queued,
                  options=options, percent=0.0)
        self.jobs[job_id] = job
        self._cancels[job_id] = threading.Event()
        # 记录输入路径
        self._inputs[job_id] = input_path
        self._q.put(job_id)
        return job

    def get(self, job_id: str) -> Optional[Job]:
        return self.jobs.get(job_id)

    def cancel(self, job_id: str) -> bool:
        ev = self._cancels.get(job_id)
        if ev:
            ev.set()
            return True
        return False

    # ---- worker ----
    def _run(self) -> None:
        while not self._stop.is_set():
            job_id = self._q.get()
            if self._stop.is_set() or not job_id:
                break
            job = self.jobs.get(job_id)
            if not job:
                continue
            # 排队期间就被取消：不必再解码/推理
            ev = self._cancels.get(job_id)
            if ev is not None and ev.is_set():
                self._cancelled(job)
                self._cleanup_input(job_id)
                continue
            try:
                self._process(job)
            except DecodeCancelled:  # 解码被取消（子类，必须在 DecodeError 之前捕获）
                self._cancelled(job)
            except DecodeError as e:  # 格式不支持/无音轨/解码失败 -> 面向用户的清晰错误
                self._fail(job, "decode_error", str(e))
            except Exception as e:  # noqa: BLE001
                traceback.print_exc()
                self._fail(job, "internal_error", str(e))
            finally:
                # 终态后删掉该任务的上传文件，避免长会话下 %LOCALAPPDATA% 无限增长
                self._cleanup_input(job_id)

    def _get_engine(self, model_id: str, use_gpu: bool = False, diarization: bool = False):
        key = f"{model_id}|gpu={use_gpu}|diar={diarization}"
        with self._engine_lock:
            eng = self._engines.get(key)
            if eng is None:
                eng = build_engine(model_id, use_gpu=use_gpu, diarization=diarization)
                eng.load()
                self._engines[key] = eng
            return eng

    def _process(self, job: Job) -> None:
        cancel = self._cancels.get(job.id) or threading.Event()
        input_path = self._inputs.get(job.id)
        if not input_path or not Path(input_path).exists():
            self._fail(job, "input_missing", "input file not found")
            return

        # 1) 解码
        self._set_stage(job, JobStatus.decoding, 0.0)

        def dec_progress(pct: float) -> None:
            self._emit_progress(job, "decoding", pct)

        wav, sr = decode_to_16k_mono(input_path, progress=dec_progress, cancel=cancel)
        if cancel.is_set():
            return self._cancelled(job)

        # 2) 引擎转写（VAD/ASR/punc），进度回调映射到各 stage
        eng = self._get_engine(job.options.model_id or "", job.options.use_gpu, job.options.diarization)

        def eng_progress(stage: str, pct: float) -> None:
            self._set_status_only(job, stage)
            self._emit_progress(job, stage, pct)

        if cancel.is_set():
            return self._cancelled(job)

        segments = eng.transcribe(wav, sr, job.options, progress=eng_progress, cancel=cancel)
        if cancel.is_set():
            return self._cancelled(job)

        # 3) 组装完成
        job.segments = segments
        job.status = JobStatus.done
        job.percent = 100.0
        self.broker.publish(f"job:{job.id}", {"type": "done", "job": job.model_dump(mode="json")})

    # ---- 状态/事件 helpers ----
    _STAGE_TO_STATUS = {
        "decoding": JobStatus.decoding, "vad": JobStatus.vad, "asr": JobStatus.asr,
        "punc": JobStatus.punc, "diar": JobStatus.diar, "assembling": JobStatus.assembling,
    }

    def _set_stage(self, job: Job, status: JobStatus, percent: float) -> None:
        job.status = status
        job.percent = percent
        self._emit_progress(job, status.value, percent)

    def _set_status_only(self, job: Job, stage: str) -> None:
        st = self._STAGE_TO_STATUS.get(stage)
        if st:
            job.status = st

    def _emit_progress(self, job: Job, stage: str, percent: float) -> None:
        job.percent = round(percent, 1)
        self.broker.publish(f"job:{job.id}",
                            {"type": "progress", "stage": stage, "percent": round(percent, 1)})

    def _fail(self, job: Job, code: str, message: str) -> None:
        job.status = JobStatus.error
        job.error = JobError(code=code, message=message)
        self.broker.publish(f"job:{job.id}", {"type": "error", "code": code, "message": message})

    def _cancelled(self, job: Job) -> None:
        job.status = JobStatus.error
        job.error = JobError(code="cancelled", message="job cancelled")
        self.broker.publish(f"job:{job.id}", {"type": "error", "code": "cancelled", "message": "job cancelled"})

    def _cleanup_input(self, job_id: str) -> None:
        """终态清理：删上传目录 + 丢弃 cancel 事件（避免长会话下 _cancels 无限增长）。

        输入文件不再需要（导出用的是内存里的 segments）。
        """
        self._cancels.pop(job_id, None)
        p = self._inputs.pop(job_id, None)
        if not p:
            return
        try:
            shutil.rmtree(Path(p).parent, ignore_errors=True)
        except Exception:  # noqa: BLE001
            pass

    # ---- 清理 ----
    def cleanup(self) -> None:
        root = jobs_dir()
        for d in root.glob("*"):
            if d.is_dir():
                shutil.rmtree(d, ignore_errors=True)
