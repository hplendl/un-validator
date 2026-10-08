"""Validation jobs: event log, cancellation, and a small manager that limits concurrent runs."""

from __future__ import annotations

import threading
import time
import uuid
from collections import OrderedDict
from typing import Any

from .. import config
from ..log import get_logger
from .models import jsonable

log = get_logger("jobs")

FINAL_STATES = ("done", "failed", "cancelled")


class Job:
    """Holds the event stream and results of one validation run."""

    def __init__(self, job_id: str, targets: list[str], options: dict | None = None):
        self.id = job_id
        self.targets = list(targets)
        self.options = dict(options or {})
        self.events: list[dict] = []
        self.lock = threading.Lock()
        self.status = "queued"
        self.result: dict | None = None
        self.created = time.time()
        self.started = self.created
        self.finished: float | None = None
        self._cancel = threading.Event()

    # ---- cancellation (``job.cancel = True`` is kept for older plugins/tools) ----
    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()

    @property
    def cancel(self) -> bool:
        return self.cancelled

    @cancel.setter
    def cancel(self, value: bool) -> None:
        if value:
            self._cancel.set()

    def request_cancel(self) -> None:
        self._cancel.set()

    @property
    def is_finished(self) -> bool:
        return self.finished is not None

    def emit(self, kind: str, **data: Any) -> dict:
        with self.lock:
            ev = jsonable({"seq": len(self.events), "t": round(time.time() - self.started, 3), "kind": kind, **data})
            self.events.append(ev)
        return ev

    def events_since(self, seq: int) -> list[dict]:
        with self.lock:
            return self.events[max(0, seq) :]

    def summary(self) -> dict:
        return {"id": self.id, "status": self.status, "targets": len(self.targets), "events": len(self.events)}


class JobManager:
    """Starts jobs on worker threads; at most ``max_concurrent`` run at once, the rest queue."""

    def __init__(self, max_concurrent: int | None = None, max_kept: int | None = None):
        self.max_concurrent = max(1, max_concurrent or config.MAX_CONCURRENT_JOBS)
        self.max_kept = max(1, max_kept or config.MAX_JOBS_KEPT)
        self._slots = threading.BoundedSemaphore(self.max_concurrent)
        self._jobs: OrderedDict[str, Job] = OrderedDict()
        self._lock = threading.Lock()

    def submit(self, targets: list[str], options: dict | None = None) -> Job:
        from .runner import run_job

        job = Job(uuid.uuid4().hex[:12], targets, options)
        with self._lock:
            self._jobs[job.id] = job
            self._prune()
            ahead = sum(1 for j in self._jobs.values() if j.status in ("queued", "running") and j is not job)
        if ahead >= self.max_concurrent:
            job.emit("job_queued", position=ahead - self.max_concurrent + 1)

        def worker() -> None:
            with self._slots:
                if job.cancelled:
                    job.status = "cancelled"
                    job.finished = time.time()
                    job.emit("job_end", overall=None, grade="n/a", files={}, status="cancelled")
                    return
                run_job(job)

        threading.Thread(target=worker, name=f"unv-job-{job.id}", daemon=True).start()
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def cancel(self, job_id: str) -> bool:
        job = self.get(job_id)
        if job is None:
            return False
        job.request_cancel()
        return True

    def active(self) -> list[Job]:
        with self._lock:
            return [j for j in self._jobs.values() if j.status in ("queued", "running")]

    def _prune(self) -> None:
        finished = [jid for jid, j in self._jobs.items() if j.status in FINAL_STATES]
        while len(self._jobs) > self.max_kept and finished:
            self._jobs.pop(finished.pop(0), None)
