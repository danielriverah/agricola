"""Worker asincrono que revisa tareas vencidas a intervalos regulares."""
from __future__ import annotations

import asyncio
import logging

from app.core.runtime import get_app_config
from app.core.settings import get_settings
from app.services.scheduler import run_due_tasks

logger = logging.getLogger(__name__)


class SchedulerWorker:
    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()

    async def _loop(self) -> None:
        settings = get_settings()
        interval = settings.scheduler_interval_seconds
        logger.info("Scheduler worker iniciado (intervalo=%ss)", interval)
        while not self._stop.is_set():
            try:
                cfg = get_app_config()
                # run_due_tasks es bloqueante (MySQL/HTTP sincrono) -> a thread.
                executed = await asyncio.to_thread(run_due_tasks, cfg)
                if executed:
                    logger.info("Scheduler ejecuto %s tarea(s)", executed)
            except Exception as exc:  # pragma: no cover - defensivo
                logger.exception("Error en ciclo del scheduler: %s", exc)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=interval)
            except asyncio.TimeoutError:
                pass
        logger.info("Scheduler worker detenido.")

    def start(self) -> None:
        if self._task is None:
            self._stop.clear()
            self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            await self._task
            self._task = None


worker = SchedulerWorker()
