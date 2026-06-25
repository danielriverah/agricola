"""Lectura de variables de entorno minimas del microservicio schedule."""
from __future__ import annotations

import os
from functools import lru_cache


class Settings:
    """Variables de entorno minimas.

    Solo se requiere lo necesario para alcanzar DynamoDB y leer app_config.
    El resto de la configuracion (MySQL, timezone, targets) vive en app_config.
    """

    def __init__(self) -> None:
        # --- app_config (DynamoDB) ---
        self.app_config_table_name: str = _env("APP_CONFIG_TABLE_NAME", "app_config")
        self.app_config_item_id: str = _env("APP_CONFIG_ITEM_ID", "microservicio-schedule")
        self.app_config_item_pk: str = _env("APP_CONFIG_ITEM_PK", "config_id")

        # --- AWS / DynamoDB ---
        self.aws_region: str = _env("AWS_REGION", "us-east-1")
        # Endpoint para DynamoDB local (ej. http://dynamodb-local:8000). Vacio => AWS real.
        self.dynamodb_endpoint_url: str | None = os.getenv("DYNAMODB_ENDPOINT_URL") or None
        self.aws_access_key_id: str | None = os.getenv("AWS_ACCESS_KEY_ID") or None
        self.aws_secret_access_key: str | None = os.getenv("AWS_SECRET_ACCESS_KEY") or None

        # --- Scheduler worker ---
        self.scheduler_enabled: bool = _as_bool(os.getenv("SCHEDULER_ENABLED", "true"))
        self.scheduler_interval_seconds: int = _as_int(
            os.getenv("SCHEDULER_INTERVAL_SECONDS"),
            default=30,
        )
        # Tiempo tras el cual un lock se considera abandonado (stale) y puede retomarse.
        self.lock_timeout_seconds: int = _as_int(
            os.getenv("LOCK_TIMEOUT_SECONDS"),
            default=300,
        )

        # --- HTTP a microservicios destino ---
        self.default_request_timeout_seconds: int = _as_int(
            os.getenv("DEFAULT_REQUEST_TIMEOUT_SECONDS"),
            default=30,
        )

        # Permite arrancar la API sin DynamoDB/MySQL (solo para tests o exploracion).
        self.skip_bootstrap: bool = _as_bool(os.getenv("SKIP_BOOTSTRAP", "false"))


def _as_bool(value: str | None) -> bool:
    if value is None:
        return False
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _env(name: str, default: str) -> str:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    return value.strip()


def _as_int(value: str | None, default: int) -> int:
    if value is None or value.strip() == "":
        return default
    try:
        return int(value)
    except ValueError:
        return default


@lru_cache
def get_settings() -> Settings:
    return Settings()
