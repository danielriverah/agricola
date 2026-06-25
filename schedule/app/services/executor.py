"""Ejecutor de tareas: arma la peticion HTTP, la dispara y registra historial.

No contiene logica de negocio de otros microservicios: solo dispara y guarda.
No hace polling de job_id.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

import httpx

from app.core.app_config import AppConfig
from app.core.timeutils import compute_next_daily_run, utcnow
from app.db import repository as repo

logger = logging.getLogger(__name__)


def _is_absolute_url(endpoint: str) -> bool:
    return endpoint.startswith("http://") or endpoint.startswith("https://")


def resolve_final_url(task: dict[str, Any], cfg: AppConfig) -> tuple[Optional[str], Optional[str]]:
    """Devuelve (final_url, error). error != None si no se pudo resolver."""
    endpoint = task["endpoint"]
    if _is_absolute_url(endpoint):
        return endpoint, None

    service_name = task.get("service_name")
    if not service_name:
        return None, "endpoint relativo sin service_name"

    base = service_name if _is_absolute_url(service_name) else cfg.resolve_base_url(service_name)
    if not base:
        return None, f"service_name '{service_name}' no esta en app_config.targets"

    base = base.rstrip("/")
    path = endpoint if endpoint.startswith("/") else f"/{endpoint}"
    return f"{base}{path}", None


def build_headers(task: dict[str, Any]) -> dict[str, str]:
    headers = {"Accept": "application/json"}
    auth_type = task.get("auth_type", "none")
    token = task.get("auth_token")
    if auth_type == "bearer" and token:
        headers["Authorization"] = f"Bearer {token}"
    elif auth_type == "header" and token and task.get("auth_header_name"):
        headers[task["auth_header_name"]] = token
    return headers


def extract_job_id(response_body: Any, ruta_job_id: str) -> tuple[Optional[str], Optional[str]]:
    """Extrae job_id navegando la ruta tipo 'body.task.job_id'.

    El prefijo 'body.' se ignora (el body es la raiz). Devuelve (job_id, observacion).
    """
    path = ruta_job_id
    if path.startswith("body."):
        path = path[len("body."):]
    elif path == "body":
        path = ""

    current: Any = response_body
    if path:
        for part in path.split("."):
            if isinstance(current, dict) and part in current:
                current = current[part]
            else:
                return None, f"no se pudo extraer job_id en ruta '{ruta_job_id}'"
    if current is None:
        return None, f"job_id nulo en ruta '{ruta_job_id}'"
    return str(current), None


def _parse_response_body(resp: httpx.Response) -> Any:
    try:
        return resp.json()
    except Exception:
        text = resp.text
        return text[:5000] if text else None


def execute_task(task: dict[str, Any], cfg: AppConfig) -> dict[str, Any]:
    """Ejecuta una tarea y devuelve el registro de historial insertado.

    No actualiza contadores/locks: eso lo maneja el llamador (scheduler o run manual).
    Devuelve un dict con al menos: status, http_status_code, job_id.
    """
    now = utcnow()
    final_url, resolve_error = resolve_final_url(task, cfg)

    base_record: dict[str, Any] = {
        "schedule_task_id": task.get("schedule_task_id"),
        "fecha_ejecucion": now,
        "service_name": task.get("service_name"),
        "visibility": task.get("visibility"),
        "endpoint": task.get("endpoint"),
        "final_url": final_url,
        "method": task.get("method"),
        "http_status_code": None,
        "status": "exception",
        "job_id": None,
        "observaciones": None,
        "request_json": None,
        "response_json": None,
        "error_message": None,
    }

    if resolve_error:
        base_record["status"] = "exception"
        base_record["error_message"] = resolve_error
        repo.insert_history(base_record)
        return base_record

    method = (task.get("method") or "POST").upper()
    query = task.get("query_json") or None
    body = task.get("body_json") or None
    headers = build_headers(task)

    request_json = {"query": query, "body": body, "headers_keys": list(headers.keys())}
    base_record["request_json"] = request_json

    try:
        with httpx.Client(timeout=cfg.request_timeout_seconds) as client:
            resp = client.request(
                method,
                final_url,
                params=query,
                json=body if body is not None else None,
                headers=headers,
            )
        base_record["http_status_code"] = resp.status_code
        response_body = _parse_response_body(resp)
        base_record["response_json"] = response_body

        if 200 <= resp.status_code < 300:
            base_record["status"] = "success"
            if task.get("trae_job_id"):
                ruta = task.get("ruta_job_id") or ""
                job_id, obs = extract_job_id(response_body, ruta)
                base_record["job_id"] = job_id
                if obs:
                    base_record["observaciones"] = obs
        else:
            base_record["status"] = "http_error"
            base_record["error_message"] = f"HTTP {resp.status_code}"
    except httpx.HTTPError as exc:
        base_record["status"] = "exception"
        base_record["error_message"] = f"{type(exc).__name__}: {exc}"
    except Exception as exc:  # pragma: no cover - defensivo
        base_record["status"] = "exception"
        base_record["error_message"] = f"{type(exc).__name__}: {exc}"

    repo.insert_history(base_record)
    return base_record


def compute_post_run_state(
    task: dict[str, Any], cfg: AppConfig
) -> tuple[Optional[Any], bool]:
    """Calcula (next_run_at, completed) tras una ejecucion.

    Regla:
      - cantidad_ejecuciones alcanzada -> completed.
      - daily=true -> proximo dia a la misma hora.
      - daily=false (run_at) -> ejecucion unica -> completed.
    """
    realizadas = (task.get("ejecuciones_realizadas") or 0) + 1
    cantidad = task.get("cantidad_ejecuciones")

    if cantidad is not None and realizadas >= cantidad:
        return None, True

    if task.get("daily"):
        tz = task.get("zona_horaria") or cfg.timezone
        next_run = compute_next_daily_run(
            task["hora_ejecucion"], tz, default_tz=cfg.timezone
        )
        return next_run, False

    # run_at de una sola vez (sin limite de cantidad): se marca completada.
    return None, True
