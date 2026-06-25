"""Orquestacion de ejecucion de tareas (lock + ejecutar + actualizar estado)."""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any, Optional

from app.core.app_config import AppConfig
from app.core.settings import get_settings
from app.core.timeutils import utcnow
from app.db import repository as repo
from app.services import executor

logger = logging.getLogger(__name__)


def run_task_now(task_id: int, cfg: AppConfig) -> dict[str, Any]:
    """Ejecuta una tarea de inmediato con bloqueo. Para uso manual o del worker.

    Devuelve el registro de historial. Lanza ValueError si no existe o no es ejecutable.
    """
    settings = get_settings()
    now = utcnow()
    stale_before = now - timedelta(seconds=settings.lock_timeout_seconds)

    task = repo.get_task(task_id)
    if task is None:
        raise ValueError("tarea no encontrada")

    if not task.get("enabled"):
        raise ValueError("la tarea esta deshabilitada")
    if task.get("completed"):
        raise ValueError("la tarea ya esta completada")

    cantidad = task.get("cantidad_ejecuciones")
    realizadas = task.get("ejecuciones_realizadas") or 0
    if cantidad is not None and realizadas >= cantidad:
        raise ValueError("la tarea alcanzo cantidad_ejecuciones")

    if not repo.try_lock_task(task_id, now, stale_before):
        raise ValueError("la tarea ya esta en ejecucion (lock activo)")

    try:
        # Recargar dentro del lock para estado fresco.
        task = repo.get_task(task_id) or task
        record = executor.execute_task(task, cfg)
        next_run_at, completed = executor.compute_post_run_state(task, cfg)
        repo.finish_task_run(
            task_id,
            last_run_at=record["fecha_ejecucion"],
            next_run_at=next_run_at,
            completed=completed,
            increment=True,
        )
        return record
    except Exception:
        repo.unlock_task(task_id)
        raise


def run_due_tasks(cfg: AppConfig) -> int:
    """Busca tareas vencidas y las ejecuta. Devuelve cuantas ejecuto."""
    now = utcnow()
    due = repo.find_due_tasks(now)
    count = 0
    for task in due:
        try:
            run_task_now(task["schedule_task_id"], cfg)
            count += 1
        except ValueError as exc:
            # Lock tomado por otra instancia o estado cambiado: se ignora.
            logger.info("Tarea %s omitida: %s", task["schedule_task_id"], exc)
        except Exception as exc:  # pragma: no cover - defensivo
            logger.exception("Error ejecutando tarea %s: %s", task["schedule_task_id"], exc)
    return count
