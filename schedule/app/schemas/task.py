"""Schemas Pydantic: validacion de entrada/salida."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field, model_validator

VALID_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE"}
VALID_VISIBILITY = {"public", "internal"}
VALID_AUTH = {"none", "bearer", "header"}


def _is_absolute_url(endpoint: str) -> bool:
    return endpoint.startswith("http://") or endpoint.startswith("https://")


class TaskBase(BaseModel):
    task_name: str = Field(..., min_length=1, max_length=255)
    enabled: bool = True
    service_name: Optional[str] = None
    visibility: str = "public"
    endpoint: str = Field(..., min_length=1)
    method: str = "POST"
    query_json: Optional[dict[str, Any]] = None
    body_json: Optional[dict[str, Any]] = None
    auth_type: str = "none"
    auth_header_name: Optional[str] = None
    auth_token: Optional[str] = None
    trae_job_id: bool = False
    ruta_job_id: Optional[str] = None
    hora_ejecucion: Optional[str] = None
    zona_horaria: Optional[str] = None
    daily: bool = False
    run_at: Optional[datetime] = None
    cantidad_ejecuciones: Optional[int] = Field(default=None, ge=1)

    @model_validator(mode="after")
    def _validate(self) -> "TaskBase":
        errors: list[str] = []

        # method
        self.method = self.method.upper()
        if self.method not in VALID_METHODS:
            errors.append(f"method invalido: {self.method}")

        # visibility
        if self.visibility not in VALID_VISIBILITY:
            errors.append(f"visibility invalida: {self.visibility}")

        # auth_type
        if self.auth_type not in VALID_AUTH:
            errors.append(f"auth_type invalido: {self.auth_type}")
        if self.auth_type == "header" and not self.auth_header_name:
            errors.append("auth_header_name requerido cuando auth_type=header")
        if self.auth_type in {"bearer", "header"} and not self.auth_token:
            errors.append(f"auth_token requerido cuando auth_type={self.auth_type}")

        # endpoint / service_name
        if not _is_absolute_url(self.endpoint) and not self.service_name:
            errors.append("service_name requerido cuando endpoint es relativo")

        # job_id
        if self.trae_job_id and not self.ruta_job_id:
            errors.append("ruta_job_id requerida cuando trae_job_id=true")

        # programacion: daily vs run_at (excluyentes, ver README/decision)
        if self.daily and self.run_at is not None:
            errors.append("daily=true y run_at son excluyentes; usa solo uno")
        if self.daily and not self.hora_ejecucion:
            errors.append("hora_ejecucion requerida cuando daily=true")
        if not self.daily and self.run_at is None:
            errors.append("run_at requerida cuando daily=false")

        if errors:
            raise ValueError("; ".join(errors))
        return self


class TaskCreate(TaskBase):
    pass


class TaskUpdate(TaskBase):
    pass


class TaskOut(BaseModel):
    schedule_task_id: int
    task_name: str
    enabled: bool
    completed: bool
    service_name: Optional[str]
    visibility: str
    endpoint: str
    method: str
    query_json: Optional[dict[str, Any]]
    body_json: Optional[dict[str, Any]]
    auth_type: str
    auth_header_name: Optional[str]
    auth_token: Optional[str]
    trae_job_id: bool
    ruta_job_id: Optional[str]
    hora_ejecucion: Optional[str]
    zona_horaria: Optional[str]
    daily: bool
    run_at: Optional[datetime]
    cantidad_ejecuciones: Optional[int]
    ejecuciones_realizadas: int
    last_run_at: Optional[datetime]
    next_run_at: Optional[datetime]
    running: bool
    locked_at: Optional[datetime]
    created_at: Optional[datetime]
    updated_at: Optional[datetime]


class HistoryOut(BaseModel):
    schedule_historia_id: int
    schedule_task_id: Optional[int]
    fecha_ejecucion: datetime
    service_name: Optional[str]
    visibility: Optional[str]
    endpoint: Optional[str]
    final_url: Optional[str]
    method: Optional[str]
    http_status_code: Optional[int]
    status: str
    job_id: Optional[str]
    observaciones: Optional[str]
    request_json: Optional[dict[str, Any]]
    response_json: Optional[Any]
    error_message: Optional[str]
    created_at: Optional[datetime]


class RuntimeConfigOut(BaseModel):
    config_id: str
    enabled: bool
    timezone: str
    targets: dict[str, str]
    request_timeout_seconds: int
    scheduler_enabled: bool
    scheduler_interval_seconds: int
    mysql_host: str
    mysql_database: str
