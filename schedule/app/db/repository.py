"""Repositorios: acceso a datos de schedule_task y schedule_historia."""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any, Optional

from app.db.mysql import get_connection, get_cursor

# Columnas que se guardan/leen como JSON.
_TASK_JSON_FIELDS = ("query_json", "body_json")
_HIST_JSON_FIELDS = ("request_json", "response_json")

_TASK_BOOL_FIELDS = ("enabled", "completed", "trae_job_id", "daily", "running")

_TASK_INSERT_COLUMNS = [
    "task_name", "enabled", "service_name", "visibility", "endpoint", "method",
    "query_json", "body_json", "auth_type", "auth_header_name", "auth_token",
    "trae_job_id", "ruta_job_id", "hora_ejecucion", "zona_horaria", "daily",
    "run_at", "cantidad_ejecuciones", "next_run_at",
]


def _dump_json(value: Any) -> Optional[str]:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False, default=str)


def _load_json(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return value


def _format_time_value(value: Any) -> Any:
    if isinstance(value, timedelta):
        total_seconds = int(value.total_seconds())
        hours = total_seconds // 3600
        minutes = (total_seconds % 3600) // 60
        seconds = total_seconds % 60
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    return value


def _hydrate_task(row: dict[str, Any] | None) -> Optional[dict[str, Any]]:
    if row is None:
        return None
    row["hora_ejecucion"] = _format_time_value(row.get("hora_ejecucion"))
    for f in _TASK_JSON_FIELDS:
        row[f] = _load_json(row.get(f))
    for f in _TASK_BOOL_FIELDS:
        if f in row and row[f] is not None:
            row[f] = bool(row[f])
    return row


def _hydrate_history(row: dict[str, Any] | None) -> Optional[dict[str, Any]]:
    if row is None:
        return None
    for f in _HIST_JSON_FIELDS:
        row[f] = _load_json(row.get(f))
    return row


# --------------------------------------------------------------------------- #
# schedule_task
# --------------------------------------------------------------------------- #
def create_task(data: dict[str, Any], next_run_at: Optional[datetime]) -> int:
    values = {
        "task_name": data["task_name"],
        "enabled": int(bool(data.get("enabled", True))),
        "service_name": data.get("service_name"),
        "visibility": data.get("visibility", "public"),
        "endpoint": data["endpoint"],
        "method": data.get("method", "POST"),
        "query_json": _dump_json(data.get("query_json")),
        "body_json": _dump_json(data.get("body_json")),
        "auth_type": data.get("auth_type", "none"),
        "auth_header_name": data.get("auth_header_name"),
        "auth_token": data.get("auth_token"),
        "trae_job_id": int(bool(data.get("trae_job_id", False))),
        "ruta_job_id": data.get("ruta_job_id"),
        "hora_ejecucion": data.get("hora_ejecucion"),
        "zona_horaria": data.get("zona_horaria"),
        "daily": int(bool(data.get("daily", False))),
        "run_at": data.get("run_at"),
        "cantidad_ejecuciones": data.get("cantidad_ejecuciones"),
        "next_run_at": next_run_at,
    }
    cols = ", ".join(_TASK_INSERT_COLUMNS)
    placeholders = ", ".join(["%s"] * len(_TASK_INSERT_COLUMNS))
    params = [values[c] for c in _TASK_INSERT_COLUMNS]

    with get_connection() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute(
                f"INSERT INTO schedule_task ({cols}) VALUES ({placeholders})", params
            )
            conn.commit()
            return cursor.lastrowid
        finally:
            cursor.close()


def update_task(task_id: int, data: dict[str, Any], next_run_at: Optional[datetime]) -> bool:
    values = {
        "task_name": data["task_name"],
        "enabled": int(bool(data.get("enabled", True))),
        "service_name": data.get("service_name"),
        "visibility": data.get("visibility", "public"),
        "endpoint": data["endpoint"],
        "method": data.get("method", "POST"),
        "query_json": _dump_json(data.get("query_json")),
        "body_json": _dump_json(data.get("body_json")),
        "auth_type": data.get("auth_type", "none"),
        "auth_header_name": data.get("auth_header_name"),
        "auth_token": data.get("auth_token"),
        "trae_job_id": int(bool(data.get("trae_job_id", False))),
        "ruta_job_id": data.get("ruta_job_id"),
        "hora_ejecucion": data.get("hora_ejecucion"),
        "zona_horaria": data.get("zona_horaria"),
        "daily": int(bool(data.get("daily", False))),
        "run_at": data.get("run_at"),
        "cantidad_ejecuciones": data.get("cantidad_ejecuciones"),
        "next_run_at": next_run_at,
    }
    set_clause = ", ".join(f"{c} = %s" for c in values)
    params = list(values.values()) + [task_id]
    with get_connection() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute(
                f"UPDATE schedule_task SET {set_clause} WHERE schedule_task_id = %s",
                params,
            )
            conn.commit()
            return cursor.rowcount > 0
        finally:
            cursor.close()


def get_task(task_id: int) -> Optional[dict[str, Any]]:
    with get_cursor() as cursor:
        cursor.execute(
            "SELECT * FROM schedule_task WHERE schedule_task_id = %s", (task_id,)
        )
        return _hydrate_task(cursor.fetchone())


def list_tasks(
    enabled: Optional[bool] = None, limit: int = 200, offset: int = 0
) -> list[dict[str, Any]]:
    where = ""
    params: list[Any] = []
    if enabled is not None:
        where = "WHERE enabled = %s"
        params.append(int(enabled))
    params.extend([limit, offset])
    with get_cursor() as cursor:
        cursor.execute(
            f"SELECT * FROM schedule_task {where} "
            "ORDER BY schedule_task_id DESC LIMIT %s OFFSET %s",
            params,
        )
        return [_hydrate_task(r) for r in cursor.fetchall()]


def delete_task(task_id: int) -> bool:
    with get_connection() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute(
                "DELETE FROM schedule_task WHERE schedule_task_id = %s", (task_id,)
            )
            conn.commit()
            return cursor.rowcount > 0
        finally:
            cursor.close()


def set_enabled(task_id: int, enabled: bool) -> bool:
    with get_connection() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute(
                "UPDATE schedule_task SET enabled = %s WHERE schedule_task_id = %s",
                (int(enabled), task_id),
            )
            conn.commit()
            return cursor.rowcount > 0
        finally:
            cursor.close()


def find_due_tasks(now: datetime) -> list[dict[str, Any]]:
    """Tareas habilitadas, no completadas, no corriendo y vencidas."""
    with get_cursor() as cursor:
        cursor.execute(
            """
            SELECT * FROM schedule_task
            WHERE enabled = 1
              AND completed = 0
              AND running = 0
              AND next_run_at IS NOT NULL
              AND next_run_at <= %s
            ORDER BY next_run_at ASC
            """,
            (now,),
        )
        return [_hydrate_task(r) for r in cursor.fetchall()]


def try_lock_task(task_id: int, now: datetime, stale_before: datetime) -> bool:
    """Bloqueo atomico: marca running=1 solo si no esta corriendo o el lock es stale.

    Evita doble ejecucion entre instancias. Devuelve True si esta instancia
    obtuvo el lock.
    """
    with get_connection() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute(
                """
                UPDATE schedule_task
                SET running = 1, locked_at = %s
                WHERE schedule_task_id = %s
                  AND enabled = 1
                  AND completed = 0
                  AND (running = 0 OR locked_at IS NULL OR locked_at < %s)
                """,
                (now, task_id, stale_before),
            )
            conn.commit()
            return cursor.rowcount == 1
        finally:
            cursor.close()


def finish_task_run(
    task_id: int,
    *,
    last_run_at: datetime,
    next_run_at: Optional[datetime],
    completed: bool,
    increment: bool,
) -> None:
    """Libera el lock y actualiza contadores/fechas tras una ejecucion."""
    inc = "ejecuciones_realizadas = ejecuciones_realizadas + 1," if increment else ""
    with get_connection() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute(
                f"""
                UPDATE schedule_task
                SET running = 0,
                    locked_at = NULL,
                    {inc}
                    last_run_at = %s,
                    next_run_at = %s,
                    completed = %s
                WHERE schedule_task_id = %s
                """,
                (last_run_at, next_run_at, int(completed), task_id),
            )
            conn.commit()
        finally:
            cursor.close()


def unlock_task(task_id: int) -> None:
    with get_connection() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute(
                "UPDATE schedule_task SET running = 0, locked_at = NULL "
                "WHERE schedule_task_id = %s",
                (task_id,),
            )
            conn.commit()
        finally:
            cursor.close()


# --------------------------------------------------------------------------- #
# schedule_historia
# --------------------------------------------------------------------------- #
_HIST_COLUMNS = [
    "schedule_task_id", "fecha_ejecucion", "service_name", "visibility",
    "endpoint", "final_url", "method", "http_status_code", "status", "job_id",
    "observaciones", "request_json", "response_json", "error_message",
]


def insert_history(record: dict[str, Any]) -> int:
    values = dict(record)
    values["request_json"] = _dump_json(values.get("request_json"))
    values["response_json"] = _dump_json(values.get("response_json"))
    cols = ", ".join(_HIST_COLUMNS)
    placeholders = ", ".join(["%s"] * len(_HIST_COLUMNS))
    params = [values.get(c) for c in _HIST_COLUMNS]
    with get_connection() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute(
                f"INSERT INTO schedule_historia ({cols}) VALUES ({placeholders})",
                params,
            )
            conn.commit()
            return cursor.lastrowid
        finally:
            cursor.close()


def list_history(
    task_id: Optional[int] = None, limit: int = 100, offset: int = 0
) -> list[dict[str, Any]]:
    where = ""
    params: list[Any] = []
    if task_id is not None:
        where = "WHERE schedule_task_id = %s"
        params.append(task_id)
    params.extend([limit, offset])
    with get_cursor() as cursor:
        cursor.execute(
            f"SELECT * FROM schedule_historia {where} "
            "ORDER BY schedule_historia_id DESC LIMIT %s OFFSET %s",
            params,
        )
        return [_hydrate_history(r) for r in cursor.fetchall()]


def get_history(history_id: int) -> Optional[dict[str, Any]]:
    with get_cursor() as cursor:
        cursor.execute(
            "SELECT * FROM schedule_historia WHERE schedule_historia_id = %s",
            (history_id,),
        )
        return _hydrate_history(cursor.fetchone())


def get_last_history_for_task(task_id: int) -> Optional[dict[str, Any]]:
    with get_cursor() as cursor:
        cursor.execute(
            "SELECT * FROM schedule_historia WHERE schedule_task_id = %s "
            "ORDER BY schedule_historia_id DESC LIMIT 1",
            (task_id,),
        )
        return _hydrate_history(cursor.fetchone())
