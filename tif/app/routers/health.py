"""Router de salud y configuración."""

from __future__ import annotations

from fastapi import APIRouter

from app.config.provider import get_config_provider
from app.db.mysql_client import MySQLReadOnlyClient

router = APIRouter(tags=["health-config"])


@router.get("/health", summary="Estado del servicio (degradado si falta config)")
def health() -> dict:
    provider = get_config_provider()
    cfg = provider.get()
    ready = cfg.is_ready()

    # Ping ligero a MySQL solo si la config mínima existe.
    mysql_ok = None
    if cfg.mysql.is_usable():
        try:
            mysql_ok = MySQLReadOnlyClient(cfg.mysql).ping()
        except Exception:  # noqa: BLE001
            mysql_ok = False

    return {
        "service": "agro-tif",
        "status": cfg.status_label(),
        "ready": ready,
        "config_present": cfg.raw_present,
        "enabled": cfg.enabled,
        "checks": {
            "mysql_configured": cfg.mysql.is_usable(),
            "mysql_reachable": mysql_ok,
            "storage_configured": cfg.storage.is_usable(),
            "earth_search_configured": cfg.earth_search.is_usable(),
        },
        "errors": [i.as_dict() for i in cfg.errors()],
        "warnings": [i.as_dict() for i in cfg.warnings()],
        "db_writes": False,
    }


@router.get("/config/view", summary="Configuración cargada (sin secretos)")
def config_view() -> dict:
    cfg = get_config_provider().get()
    from app.config.env_settings import get_env_settings

    return {
        "env": get_env_settings().as_safe_dict(),
        "app_config": cfg.safe_dict(),
        "status": cfg.status_label(),
    }


@router.get("/internal/config/validate", summary="Lista campos faltantes o inválidos")
def config_validate() -> dict:
    cfg = get_config_provider().get()
    return {
        "valid": cfg.is_ready(),
        "status": cfg.status_label(),
        "errors": [i.as_dict() for i in cfg.errors()],
        "warnings": [i.as_dict() for i in cfg.warnings()],
    }


@router.post("/internal/config/refresh", summary="Recarga app_config desde DynamoDB")
def config_refresh() -> dict:
    cfg = get_config_provider().refresh()
    return {
        "refreshed": True,
        "status": cfg.status_label(),
        "errors": [i.as_dict() for i in cfg.errors()],
        "warnings": [i.as_dict() for i in cfg.warnings()],
    }
