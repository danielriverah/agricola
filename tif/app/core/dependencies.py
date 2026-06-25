"""
Dependencias FastAPI reutilizables.

Implementan la regla del README:
    "Endpoints que requieran MySQL/S3/Earth Search deben responder 503 o
     validation_error indicando la clave faltante."
"""

from __future__ import annotations

from app.config.models import AppConfig
from app.config.provider import get_config
from app.core.errors import ConfigUnavailableError


def config_dep() -> AppConfig:
    return get_config()


def require_mysql() -> AppConfig:
    cfg = get_config()
    if not cfg.mysql.is_usable():
        missing = [i.field for i in cfg.errors() if i.field.startswith("mysql")]
        raise ConfigUnavailableError(missing or ["mysql"], layer="mysql")
    return cfg


def require_storage() -> AppConfig:
    cfg = get_config()
    if not cfg.storage.is_usable():
        raise ConfigUnavailableError(["storage.s3_bucket"], layer="storage")
    return cfg


def require_earth_search() -> AppConfig:
    cfg = get_config()
    if not cfg.earth_search.is_usable():
        raise ConfigUnavailableError(
            ["earth_search.search_url", "earth_search.collection"], layer="earth_search"
        )
    return cfg
