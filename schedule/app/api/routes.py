"""Endpoints HTTP del microservicio schedule."""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Response

from app.core.app_config import AppConfig
from app.core.runtime import (
    get_app_config,
    get_bootstrap_errors,
    has_app_config,
    has_mysql,
    is_ready,
)
from app.core.settings import get_settings
from app.core.timeutils import compute_next_daily_run, normalize_run_at
from app.db import repository as repo
from app.schemas.task import (
    HistoryOut,
    RuntimeConfigOut,
    TaskCreate,
    TaskOut,
    TaskUpdate,
)
from app.services.scheduler import run_task_now

logger = logging.getLogger(__name__)
router = APIRouter()


def _require_cfg() -> AppConfig:
    if not has_app_config():
        errors = get_bootstrap_errors()
        detail = {
            "message": "app_config no disponible",
            "errors": errors or ["No se ha cargado app_config."],
        }
        raise HTTPException(status_code=503, detail=detail)
    return get_app_config()


def _require_mysql() -> None:
    if not has_mysql():
        errors = get_bootstrap_errors()
        detail = {
            "message": "MySQL no disponible",
            "errors": errors or ["Pool MySQL no inicializado."],
        }
        raise HTTPException(status_code=503, detail=detail)


def _compute_next_run(data: dict, cfg: AppConfig):
    """Calcula next_run_at inicial al crear/actualizar."""
    if data.get("daily"):
        tz = data.get("zona_horaria") or cfg.timezone
        return compute_next_daily_run(data["hora_ejecucion"], tz, default_tz=cfg.timezone)
    # run_at puntual
    run_at = data.get("run_at")
    if run_at is None:
        return None
    tz = data.get("zona_horaria") or cfg.timezone
    return normalize_run_at(run_at, tz, default_tz=cfg.timezone)


# --------------------------------------------------------------------------- #
# Health & runtime config
# --------------------------------------------------------------------------- #
@router.get("/health")
def health() -> dict:
    errors = get_bootstrap_errors()
    return {
        "status": "ok" if is_ready() else "degraded",
        "ready": is_ready(),
        "app_config_loaded": has_app_config(),
        "mysql_ready": has_mysql(),
        "errors": errors,
    }


@router.get("/config/runtime", response_model=RuntimeConfigOut)
def runtime_config() -> RuntimeConfigOut:
    cfg = _require_cfg()
    settings = get_settings()
    return RuntimeConfigOut(
        config_id=cfg.config_id,
        enabled=cfg.enabled,
        timezone=cfg.timezone,
        targets=cfg.targets,
        request_timeout_seconds=cfg.request_timeout_seconds,
        scheduler_enabled=settings.scheduler_enabled,
        scheduler_interval_seconds=settings.scheduler_interval_seconds,
        mysql_host=cfg.mysql.host,
        mysql_database=cfg.mysql.database,
    )


# --------------------------------------------------------------------------- #
# Tasks CRUD
# --------------------------------------------------------------------------- #
@router.get("/tasks", response_model=list[TaskOut])
def list_tasks(
    enabled: Optional[bool] = None,
    limit: int = Query(200, ge=1, le=1000),
    offset: int = Query(0, ge=0),
) -> list[dict]:
    _require_mysql()
    return repo.list_tasks(enabled=enabled, limit=limit, offset=offset)


@router.post("/tasks", response_model=TaskOut, status_code=201)
def create_task(payload: TaskCreate) -> dict:
    cfg = _require_cfg()
    _require_mysql()
    data = payload.model_dump()
    next_run_at = _compute_next_run(data, cfg)
    task_id = repo.create_task(data, next_run_at)
    return repo.get_task(task_id)


@router.get("/tasks/{task_id}", response_model=TaskOut)
def get_task(task_id: int) -> dict:
    _require_mysql()
    task = repo.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="tarea no encontrada")
    return task


@router.put("/tasks/{task_id}", response_model=TaskOut)
def update_task(task_id: int, payload: TaskUpdate) -> dict:
    cfg = _require_cfg()
    _require_mysql()
    if repo.get_task(task_id) is None:
        raise HTTPException(status_code=404, detail="tarea no encontrada")
    data = payload.model_dump()
    next_run_at = _compute_next_run(data, cfg)
    repo.update_task(task_id, data, next_run_at)
    return repo.get_task(task_id)


@router.delete("/tasks/{task_id}", status_code=204)
def delete_task(task_id: int) -> Response:
    _require_mysql()
    if not repo.delete_task(task_id):
        raise HTTPException(status_code=404, detail="tarea no encontrada")
    return Response(status_code=204)


@router.post("/tasks/{task_id}/run", response_model=HistoryOut)
def run_task(task_id: int) -> dict:
    cfg = _require_cfg()
    _require_mysql()
    try:
        record = run_task_now(task_id, cfg)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    # Devolver el registro de historial completo (con id).
    last = repo.get_last_history_for_task(task_id)
    return last or record


# --------------------------------------------------------------------------- #
# History
# --------------------------------------------------------------------------- #
@router.get("/history", response_model=list[HistoryOut])
def list_history(
    task_id: Optional[int] = None,
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
) -> list[dict]:
    _require_mysql()
    return repo.list_history(task_id=task_id, limit=limit, offset=offset)


@router.get("/history/{history_id}", response_model=HistoryOut)
def get_history(history_id: int) -> dict:
    _require_mysql()
    record = repo.get_history(history_id)
    if record is None:
        raise HTTPException(status_code=404, detail="historial no encontrado")
    return record
