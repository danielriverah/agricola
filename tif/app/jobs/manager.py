"""
Gestor de jobs BAJO DEMANDA.

IMPORTANTE (README): TIF no tiene scheduler, cron ni daemon recurrente. Los jobs
existen solo para ejecutar trabajo pesado disparado explícitamente por un POST.
Aquí no hay ningún hilo que se auto-encole por tiempo: los workers se crean al
recibir la petición y mueren al terminar.
"""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable


class JobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class Job:
    id: str
    kind: str
    status: JobStatus = JobStatus.PENDING
    total: int = 0
    processed: int = 0
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    finished_at: float | None = None
    results: list = field(default_factory=list)
    error: str | None = None
    progress_note: str | None = None
    _cancel: threading.Event = field(default_factory=threading.Event)

    @property
    def progress(self) -> float:
        if self.total <= 0:
            return 0.0
        return round(100.0 * self.processed / self.total, 2)

    def cancel_requested(self) -> bool:
        return self._cancel.is_set()

    def as_dict(self) -> dict:
        return {
            "job_id": self.id,
            "kind": self.kind,
            "status": self.status.value,
            "total": self.total,
            "processed": self.processed,
            "progress": self.progress,
            "progress_note": self.progress_note,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "error": self.error,
            "results": self.results,
            "cancel_requested": self.cancel_requested(),
        }


class JobManager:
    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._heavy_lock = threading.Lock()
        self._active_heavy_job_id: str | None = None

    def create(self, kind: str, total: int = 0) -> Job:
        job = Job(id=uuid.uuid4().hex, kind=kind, total=total)
        with self._lock:
            self._jobs[job.id] = job
        return job

    def try_create_heavy(self, kind: str, total: int = 0) -> tuple[Job | None, Job | None]:
        acquired = self._heavy_lock.acquire(blocking=False)
        if not acquired:
            return None, self.active_heavy()
        job = self.create(kind=kind, total=total)
        with self._lock:
            self._active_heavy_job_id = job.id
        return job, None

    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def list(self) -> list[Job]:
        return list(self._jobs.values())

    def active(self) -> list[Job]:
        return [j for j in self._jobs.values() if j.status in (JobStatus.PENDING, JobStatus.RUNNING)]

    def active_heavy(self) -> Job | None:
        with self._lock:
            if not self._active_heavy_job_id:
                return None
            return self._jobs.get(self._active_heavy_job_id)

    def cancel(self, job_id: str) -> bool:
        job = self.get(job_id)
        if not job:
            return False
        if job.status in (JobStatus.DONE, JobStatus.FAILED, JobStatus.CANCELLED):
            return False
        job._cancel.set()
        return True

    def note(self, job_id: str, message: str | None) -> None:
        job = self.get(job_id)
        if job:
            job.progress_note = message

    def run_async(self, job: Job, work: Callable[[Job], None]) -> None:
        """Lanza el trabajo en un hilo efímero (no recurrente)."""

        def runner() -> None:
            job.status = JobStatus.RUNNING
            job.started_at = time.time()
            try:
                work(job)
                job.status = JobStatus.CANCELLED if job.cancel_requested() else JobStatus.DONE
            except Exception as exc:  # noqa: BLE001
                job.status = JobStatus.FAILED
                job.error = str(exc)
            finally:
                job.finished_at = time.time()
                with self._lock:
                    if self._active_heavy_job_id == job.id:
                        self._active_heavy_job_id = None
                        self._heavy_lock.release()

        threading.Thread(target=runner, daemon=True, name=f"job-{job.id}").start()

    def stats(self) -> dict:
        counts: dict[str, int] = {}
        for j in self._jobs.values():
            counts[j.status.value] = counts.get(j.status.value, 0) + 1
        return {"total": len(self._jobs), "by_status": counts}


# Singleton de proceso.
_manager: JobManager | None = None


def get_job_manager() -> JobManager:
    global _manager
    if _manager is None:
        _manager = JobManager()
    return _manager
