"""
Codigos de error estandarizados del servicio.

Mapeo a HTTP:
  - sync_busy        -> 409
  - already_synced   -> 200 (no es error, es estado informativo)
  - not_found        -> 404
  - validation_error -> 422
"""
from fastapi import HTTPException, status


class AppError(HTTPException):
    code: str = "app_error"

    def __init__(self, message: str, http_status: int, code: str, extra: dict | None = None):
        detail = {"code": code, "message": message}
        if extra:
            detail.update(extra)
        super().__init__(status_code=http_status, detail=detail)
        self.code = code


class SyncBusyError(AppError):
    def __init__(self, active_job_id: str | None = None):
        super().__init__(
            message="Ya existe una sincronizacion pesada activa.",
            http_status=status.HTTP_409_CONFLICT,
            code="sync_busy",
            extra={"active_job_id": active_job_id} if active_job_id else None,
        )


class NotFoundError(AppError):
    def __init__(self, message: str = "Recurso no encontrado."):
        super().__init__(
            message=message,
            http_status=status.HTTP_404_NOT_FOUND,
            code="not_found",
        )


class ValidationAppError(AppError):
    def __init__(self, message: str = "Solicitud invalida."):
        super().__init__(
            message=message,
            http_status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            code="validation_error",
        )
