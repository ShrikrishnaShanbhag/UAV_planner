"""In-process job registry: progress, results and cancellation for background work."""

from __future__ import annotations

import threading
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any


class JobCancelled(Exception):
    """Raised by a job function that noticed its cancel flag."""


@dataclass(slots=True)
class Job:
    id: str
    kind: str
    status: str = "queued"
    done: int = 0
    total: int = 0
    message: str = ""
    error: str | None = None
    result: Any = None
    cancel: threading.Event = field(default_factory=threading.Event)

    def progress(self, done: int, total: int) -> None:
        self.done, self.total = done, total


class JobRegistry:
    """Runs jobs on a small thread pool. CPU-heavy jobs start their own process pool."""

    def __init__(self, workers: int = 2) -> None:
        self._pool = ThreadPoolExecutor(max_workers=workers)
        self._jobs: dict[str, Job] = {}

    def submit(
        self,
        kind: str,
        function: Callable[[Job], Any],
        on_done: Callable[[Job], None] | None = None,
        job_id: str | None = None,
    ) -> Job:
        job = Job(id=job_id or uuid.uuid4().hex[:12], kind=kind)
        self._jobs[job.id] = job

        def runner() -> None:
            if job.cancel.is_set():
                job.status = "cancelled"
            else:
                job.status = "running"
                try:
                    job.result = function(job)
                    job.status = "cancelled" if job.cancel.is_set() else "finished"
                except JobCancelled:
                    job.status = "cancelled"
                except Exception as error:
                    job.status = "failed"
                    job.error = f"{type(error).__name__}: {error}"
            if on_done is not None:
                on_done(job)

        self._pool.submit(runner)
        return job

    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def cancel(self, job_id: str) -> Job | None:
        job = self._jobs.get(job_id)
        if job is not None:
            job.cancel.set()
        return job

    def shutdown(self) -> None:
        for job in self._jobs.values():
            job.cancel.set()
        self._pool.shutdown(wait=False, cancel_futures=True)
