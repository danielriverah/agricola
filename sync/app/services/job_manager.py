"""
Gestor de jobs de sincronizacion pesada.

Principios del README:
  - Una sola sincronizacion pesada activa a la vez (lock global).
  - Las sincronizaciones pesadas se bloquean si ya hay una corriendo (409 sync_busy).
  - Se pueden consultar jobs activos, terminados y cancelarlos.

Las lecturas (GET) NO pasan por aqui: responden directo sin lock.
"""
import threading
import time
import uuid
import copy
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Callable, Optional

from app.core.errors import SyncBusyError, NotFoundError
from app.core.logging_config import get_logger

log = get_logger(__name__)


class JobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    ERROR = "error"
    CANCELLED = "cancelled"


@dataclass
class Job:
    job_id: str
    kind: str
    dry_run: bool
    params: dict[str, Any] = field(default_factory=dict)
    status: JobStatus = JobStatus.PENDING
    created_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    result: dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None
    cancel_requested: bool = False
    progress: int = 0
    progress_note: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["status"] = self.status.value
        return d


class JobManager:
    def __init__(self):
        self._lock = threading.Lock()          # protege el registro
        self._heavy_lock = threading.Lock()    # garantiza 1 sync pesada
        self._jobs: dict[str, Job] = {}
        self._active_job_id: Optional[str] = None

    def _new_id(self, kind: str) -> str:
        return f"{kind}_{int(time.time())}_{uuid.uuid4().hex[:6]}"

    def active_job(self) -> Optional[Job]:
        with self._lock:
            if self._active_job_id:
                return self._jobs.get(self._active_job_id)
            return None

    def set_progress(self, job_id: str, progress: int, note: Optional[str] = None) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return
            job.progress = max(0, min(100, int(progress)))
            job.progress_note = note

    def list_jobs(self) -> list[dict]:
        with self._lock:
            return [j.to_dict() for j in sorted(
                self._jobs.values(), key=lambda x: x.created_at, reverse=True
            )]

    def get_job(self, job_id: str) -> dict:
        with self._lock:
            job = self._jobs.get(job_id)
        if not job:
            raise NotFoundError(f"Job {job_id} no encontrado.")
        return job.to_dict()

    def request_cancel(self, job_id: str) -> dict:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                raise NotFoundError(f"Job {job_id} no encontrado.")
            if job.status in (JobStatus.DONE, JobStatus.ERROR, JobStatus.CANCELLED):
                return job.to_dict()
            job.cancel_requested = True
            if job.status == JobStatus.PENDING:
                job.status = JobStatus.CANCELLED
                job.finished_at = time.time()
            log.info("Cancelacion solicitada para job %s", job_id)
            return job.to_dict()

    def run_heavy(
        self,
        kind: str,
        dry_run: bool,
        fn: Callable[[Job], dict],
        params: Optional[dict[str, Any]] = None,
    ) -> dict:
        """
        Ejecuta una sincronizacion pesada en segundo plano protegida por lock.
        Si ya hay una corriendo, lanza SyncBusyError (409).
        """
        acquired = self._heavy_lock.acquire(blocking=False)
        if not acquired:
            active = self.active_job()
            raise SyncBusyError(active.job_id if active else None)

        job = Job(job_id=self._new_id(kind), kind=kind, dry_run=dry_run, params=params or {})
        with self._lock:
            self._jobs[job.job_id] = job
            self._active_job_id = job.job_id

        def _runner() -> None:
            try:
                job.status = JobStatus.RUNNING
                job.started_at = time.time()
                log.info("Job %s iniciado (kind=%s dry_run=%s)", job.job_id, kind, dry_run)
                result = fn(job)
                job.result = copy.deepcopy(result or {})
                if job.cancel_requested:
                    job.status = JobStatus.CANCELLED
                else:
                    job.status = JobStatus.DONE
            except Exception as exc:  # noqa: BLE001
                job.status = JobStatus.ERROR
                job.error = str(exc)
                log.exception("Job %s fallo", job.job_id)
            finally:
                job.finished_at = time.time()
                with self._lock:
                    self._active_job_id = None
                self._heavy_lock.release()

        threading.Thread(target=_runner, daemon=True).start()
        return job.to_dict()


_manager: Optional[JobManager] = None


def get_job_manager() -> JobManager:
    global _manager
    if _manager is None:
        _manager = JobManager()
    return _manager
