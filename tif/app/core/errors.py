"""Errores y helpers compartidos."""

from __future__ import annotations

from fastapi import HTTPException, status


class ConfigUnavailableError(HTTPException):
    """503 cuando una capa requerida no está configurada."""

    def __init__(self, missing: list[str], layer: str) -> None:
        super().__init__(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error": "configuration_unavailable",
                "layer": layer,
                "missing": missing,
                "message": f"La capa '{layer}' no está configurada correctamente.",
            },
        )


class ValidationError(HTTPException):
    def __init__(self, message: str, field: str | None = None) -> None:
        super().__init__(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"error": "validation_error", "field": field, "message": message},
        )


class WriteAttemptBlocked(HTTPException):
    """410/403 para rutas legacy que intentarían escribir en DB."""

    def __init__(self, route: str) -> None:
        super().__init__(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": "write_disabled",
                "route": route,
                "message": (
                    "TIF es de solo lectura hacia bases de datos. Esta ruta legacy "
                    "no ejecuta escrituras."
                ),
            },
        )


class HeavyJobBusy(HTTPException):
    """409 cuando ya hay una carga pesada activa."""

    def __init__(self, active_job: dict | None = None) -> None:
        super().__init__(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": "heavy_job_busy",
                "active_job": active_job,
                "message": "Ya hay una carga pesada activa. Consulta el job_id activo.",
            },
        )


class ExternalResourceError(HTTPException):
    """422 cuando falla una lectura/escritura externa con contexto útil."""

    def __init__(self, resource: str, reason: str, hint: str | None = None, detail: dict | None = None) -> None:
        payload = {
            "error": "external_resource_error",
            "resource": resource,
            "reason": reason,
            "message": f"Fallo en {resource}: {reason}",
        }
        if hint:
            payload["hint"] = hint
        if detail:
            payload["detail"] = detail
        super().__init__(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=payload)
