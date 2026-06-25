"""
Carga del .env MÍNIMO del microservicio TIF.

REGLA DEL README:
    El .env solo debe contener lo mínimo para conectarse a DynamoDB y localizar
    el registro `app_config`. NINGUNA variable operativa (MySQL, S3, Earth Search,
    parámetros de procesamiento) vive aquí: esas viven en DynamoDB -> app_config.

Este módulo NO debe fallar si falta algo: solo expone los valores y deja que la
capa de configuración runtime decida qué falta y lo reporte por API.
"""

from __future__ import annotations

import os
from functools import lru_cache

try:
    # python-dotenv es opcional: si no está, leemos del entorno directamente.
    from dotenv import load_dotenv

    load_dotenv()
except Exception:  # pragma: no cover - dotenv ausente no es crítico
    pass


def _get_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


def _get_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _get_str(name: str, default: str | None = None) -> str | None:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    return raw


class EnvSettings:
    """Solo conexión a DynamoDB + localización del item app_config."""

    def __init__(self) -> None:
        # --- Localización del registro de configuración en DynamoDB ---
        self.app_config_table_name: str = _get_str("APP_CONFIG_TABLE_NAME", "app_config")
        self.app_config_item_id: str = _get_str("APP_CONFIG_ITEM_ID", "microservicio-tif")
        self.app_config_item_pk: str = _get_str("APP_CONFIG_ITEM_PK", "config_id")

        # --- Región y endpoint de DynamoDB ---
        self.aws_region: str = _get_str("AWS_REGION", "us-east-1")
        self.dynamodb_endpoint_url: str | None = _get_str("DYNAMODB_ENDPOINT_URL", None)
        self.dynamodb_use_aws: bool = _get_bool("DYNAMODB_USE_AWS", True)

        # --- Credenciales opcionales (si no se usan roles/perfil AWS) ---
        self.aws_access_key_id: str | None = _get_str("AWS_ACCESS_KEY_ID_CUSTOM", None)
        self.aws_secret_access_key: str | None = _get_str("AWS_SECRET_ACCESS_KEY_CUSTOM", None)
        self.aws_session_token: str | None = _get_str("AWS_SESSION_TOKEN_CUSTOM", None)

        # --- Comportamiento de la capa de configuración ---
        self.config_cache_ttl_seconds: int = _get_int("CONFIG_CACHE_TTL_SECONDS", 60)
        self.config_fail_fast: bool = _get_bool("CONFIG_FAIL_FAST", False)

    def as_safe_dict(self) -> dict:
        """Representación sin secretos para /config/view."""
        return {
            "app_config_table_name": self.app_config_table_name,
            "app_config_item_id": self.app_config_item_id,
            "app_config_item_pk": self.app_config_item_pk,
            "aws_region": self.aws_region,
            "dynamodb_endpoint_url": self.dynamodb_endpoint_url,
            "dynamodb_use_aws": self.dynamodb_use_aws,
            "aws_access_key_id": "***" if self.aws_access_key_id else None,
            "aws_secret_access_key": "***" if self.aws_secret_access_key else None,
            "aws_session_token": "***" if self.aws_session_token else None,
            "config_cache_ttl_seconds": self.config_cache_ttl_seconds,
            "config_fail_fast": self.config_fail_fast,
        }


@lru_cache(maxsize=1)
def get_env_settings() -> EnvSettings:
    return EnvSettings()
