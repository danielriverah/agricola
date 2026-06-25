"""Estado runtime compartido del proceso."""
from __future__ import annotations

from app.core.app_config import AppConfig

_app_config: AppConfig | None = None
_bootstrap_errors: list[str] = []
_mysql_ready: bool = False


def set_app_config(cfg: AppConfig) -> None:
    global _app_config
    _app_config = cfg


def get_app_config() -> AppConfig:
    if _app_config is None:
        raise RuntimeError("app_config no cargado todavia.")
    return _app_config


def has_app_config() -> bool:
    return _app_config is not None


def set_mysql_ready(value: bool) -> None:
    global _mysql_ready
    _mysql_ready = value


def has_mysql() -> bool:
    return _mysql_ready


def clear_bootstrap_errors() -> None:
    _bootstrap_errors.clear()


def add_bootstrap_error(message: str) -> None:
    _bootstrap_errors.append(message)


def get_bootstrap_errors() -> list[str]:
    return list(_bootstrap_errors)


def is_ready() -> bool:
    return has_app_config() and has_mysql() and not _bootstrap_errors
