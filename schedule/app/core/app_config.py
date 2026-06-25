"""Carga de app_config desde DynamoDB y exposicion de configuracion runtime.

app_config resuelve:
  - configuracion MySQL
  - timezone default
  - targets HTTP de microservicios
  - request_timeout_seconds (opcional)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from app.core.settings import Settings, get_settings

logger = logging.getLogger(__name__)


@dataclass
class MySQLConfig:
    host: str
    port: int
    database: str
    user: str
    password: str

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MySQLConfig":
        missing = [key for key in ("host", "database", "user") if not data.get(key)]
        if missing:
            raise RuntimeError(
                "app_config.mysql incompleto. Faltan: " + ", ".join(f"mysql.{key}" for key in missing)
            )
        try:
            port = int(data.get("port", 3306))
        except (TypeError, ValueError) as exc:
            raise RuntimeError("app_config.mysql.port debe ser numerico") from exc
        return cls(
            host=str(data["host"]),
            port=port,
            database=str(data["database"]),
            user=str(data["user"]),
            password=str(data.get("password", "")),
        )


@dataclass
class AppConfig:
    """Configuracion runtime resuelta desde app_config."""

    config_id: str
    enabled: bool
    timezone: str
    targets: dict[str, str]
    mysql: MySQLConfig
    request_timeout_seconds: int
    raw: dict[str, Any] = field(default_factory=dict)

    def resolve_base_url(self, service_name: str) -> str | None:
        return self.targets.get(service_name)


def _build_dynamodb_resource(settings: Settings):
    kwargs: dict[str, Any] = {"region_name": settings.aws_region}
    if settings.dynamodb_endpoint_url:
        kwargs["endpoint_url"] = settings.dynamodb_endpoint_url
    if settings.aws_access_key_id and settings.aws_secret_access_key:
        kwargs["aws_access_key_id"] = settings.aws_access_key_id
        kwargs["aws_secret_access_key"] = settings.aws_secret_access_key
    return boto3.resource("dynamodb", **kwargs)


def load_app_config(settings: Settings | None = None) -> AppConfig:
    """Lee el item de app_config desde DynamoDB y lo normaliza."""
    settings = settings or get_settings()

    try:
        dynamodb = _build_dynamodb_resource(settings)
        table = dynamodb.Table(settings.app_config_table_name)
        response = table.get_item(
            Key={settings.app_config_item_pk: settings.app_config_item_id}
        )
    except (BotoCoreError, ClientError) as exc:  # pragma: no cover - depende de infra
        raise RuntimeError(
            f"No se pudo leer app_config desde DynamoDB: {exc}"
        ) from exc

    item = response.get("Item")
    if not item:
        raise RuntimeError(
            "app_config no encontrado: "
            f"tabla={settings.app_config_table_name} "
            f"{settings.app_config_item_pk}={settings.app_config_item_id}"
        )

    return _parse_app_config(item, settings)


def _parse_app_config(item: dict[str, Any], settings: Settings) -> AppConfig:
    mysql_raw = item.get("mysql")
    if not mysql_raw:
        raise RuntimeError("app_config no contiene la seccion 'mysql'")
    if not isinstance(mysql_raw, dict):
        raise RuntimeError("app_config.mysql debe ser un objeto")

    targets_raw = item.get("targets") or {}
    if not isinstance(targets_raw, dict):
        raise RuntimeError("app_config.targets debe ser un objeto")

    request_timeout = item.get("request_timeout_seconds")
    try:
        request_timeout = int(request_timeout)
    except (TypeError, ValueError):
        request_timeout = settings.default_request_timeout_seconds

    return AppConfig(
        config_id=str(item.get(settings.app_config_item_pk, settings.app_config_item_id)),
        enabled=bool(item.get("enabled", True)),
        timezone=str(item.get("timezone", "America/Mexico_City")),
        targets={str(k): str(v) for k, v in targets_raw.items()},
        mysql=MySQLConfig.from_dict(dict(mysql_raw)),
        request_timeout_seconds=request_timeout,
        raw=dict(item),
    )
