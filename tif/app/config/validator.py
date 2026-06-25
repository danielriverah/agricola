"""
Conversión tolerante del item crudo de DynamoDB -> AppConfig + validación.

Genera la lista de `issues` que consumen /health y /internal/config/validate.
Nunca lanza excepción por datos faltantes: ese es justamente el punto del
arranque degradado.
"""

from __future__ import annotations

from typing import Any

from app.config.models import (
    AppConfig,
    ConfigIssue,
    EarthSearchConfig,
    MySQLConfig,
    OutputsConfig,
    ProcessingConfig,
    SecurityConfig,
    StorageConfig,
)


def _as_dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _coerce_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _coerce_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _coerce_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    if isinstance(value, (int, float)):
        return bool(value)
    return default


def parse_app_config(raw: dict | None) -> AppConfig:
    """Construye AppConfig desde el item DynamoDB y acumula issues."""
    cfg = AppConfig()

    if not raw:
        cfg.raw_present = False
        cfg.issues.append(
            ConfigIssue(
                field="app_config",
                severity="error",
                message="No se encontró el registro app_config en DynamoDB.",
            )
        )
        return cfg

    cfg.raw_present = True
    cfg.config_id = raw.get("config_id")
    cfg.enabled = _coerce_bool(raw.get("enabled"), True)
    cfg.timezone = raw.get("timezone") or "America/Mexico_City"
    cfg.request_timeout_seconds = _coerce_int(raw.get("request_timeout_seconds"), 60)

    # ---- MySQL ----
    m = _as_dict(raw.get("mysql"))
    cfg.mysql = MySQLConfig(
        host=m.get("host"),
        port=_coerce_int(m.get("port"), 3306),
        database=m.get("database"),
        user=m.get("user"),
        password=m.get("password"),
        strict_mode=_coerce_bool(m.get("strict_mode"), True),
    )

    # ---- Storage ----
    s = _as_dict(raw.get("storage"))
    cfg.storage = StorageConfig(
        driver=s.get("driver") or "s3",
        s3_bucket=s.get("s3_bucket"),
        base_path=s.get("base_path") or "previews/PROD_{production_id}/{scene_name}",
        public_url_ttl_minutes=_coerce_int(s.get("public_url_ttl_minutes"), 60),
    )

    # ---- Earth Search / STAC ----
    es = _as_dict(raw.get("earth_search"))
    asset_map = es.get("asset_map")
    if not isinstance(asset_map, dict) or not asset_map:
        asset_map = EarthSearchConfig().asset_map
    band_resolution_meters = es.get("band_resolution_meters")
    if not isinstance(band_resolution_meters, dict) or not band_resolution_meters:
        band_resolution_meters = EarthSearchConfig().band_resolution_meters
    cfg.earth_search = EarthSearchConfig(
        search_url=es.get("search_url") or "https://earth-search.aws.element84.com/v1/search",
        collection=es.get("collection") or "sentinel-2-l2a",
        max_cloud_coverage=_coerce_int(es.get("max_cloud_coverage"), 100),
        request_timeout_seconds=_coerce_int(es.get("request_timeout_seconds"), 120),
        band_resolution_meters={str(k): _coerce_int(v, 10) for k, v in band_resolution_meters.items()},
        asset_map={str(k): str(v) for k, v in asset_map.items()},
    )

    # ---- Processing ----
    p = _as_dict(raw.get("processing"))
    indices = p.get("default_indices")
    if not isinstance(indices, list) or not indices:
        indices = ProcessingConfig().default_indices
    cfg.processing = ProcessingConfig(
        default_indices=[str(i) for i in indices],
        resolution_meters=_coerce_int(p.get("resolution_meters"), 10),
        apply_cloud_mask=_coerce_bool(p.get("apply_cloud_mask"), True),
        max_production_cloud=_coerce_float(p.get("max_production_cloud"), 20),
        min_valid_pixels_percentage=_coerce_float(p.get("min_valid_pixels_percentage"), 1),
        generate_png=_coerce_bool(p.get("generate_png"), True),
        generate_geotiff=_coerce_bool(p.get("generate_geotiff"), True),
        generate_pdf=_coerce_bool(p.get("generate_pdf"), False),
    )

    # ---- Outputs ----
    o = _as_dict(raw.get("outputs"))
    cfg.outputs = OutputsConfig(
        multiband_filename=o.get("multiband_filename") or "multiband.tif",
        params_filename=o.get("params_filename") or "multiband.params.json",
        temp_prune_on_scene_select=_coerce_bool(o.get("temp_prune_on_scene_select"), True),
        temp_clean_expired_on_scene_select=_coerce_bool(
            o.get("temp_clean_expired_on_scene_select"), True
        ),
        temp_expire_ttl_hours=_coerce_int(o.get("temp_expire_ttl_hours"), 24),
    )

    # ---- Targets (no debe usarse para disparar IA automáticamente) ----
    cfg.targets = _as_dict(raw.get("targets"))

    # ---- Security ----
    sec = _as_dict(raw.get("security"))
    cfg.security = SecurityConfig(api_secret_key=sec.get("api_secret_key") or None)

    # ---- Validación / generación de issues ----
    _validate(cfg)
    return cfg


def _validate(cfg: AppConfig) -> None:
    """Acumula issues. 'error' = la capa correspondiente no es usable."""
    # MySQL es lo más crítico: casi todo el servicio lee de ahí.
    if not cfg.mysql.host:
        cfg.issues.append(ConfigIssue("mysql.host", "error", "Falta el host de MySQL."))
    if not cfg.mysql.database:
        cfg.issues.append(ConfigIssue("mysql.database", "error", "Falta la base de datos MySQL."))
    if cfg.mysql.user is None:
        cfg.issues.append(ConfigIssue("mysql.user", "error", "Falta el usuario MySQL."))
    if cfg.mysql.password is None:
        cfg.issues.append(
            ConfigIssue("mysql.password", "warning", "Contraseña MySQL vacía/ausente.")
        )

    # Storage: requerido para generar archivos, pero la lectura de MySQL puede
    # funcionar sin S3 -> warning, no error global.
    if cfg.storage.driver == "s3" and not cfg.storage.s3_bucket:
        cfg.issues.append(
            ConfigIssue(
                "storage.s3_bucket",
                "warning",
                "Driver s3 sin bucket: la generación de archivos no funcionará.",
            )
        )

    # Earth Search/STAC: necesario para descargar bandas reales -> warning.
    if not cfg.earth_search.search_url or not cfg.earth_search.collection:
        cfg.issues.append(
            ConfigIssue(
                "earth_search.search_url/collection",
                "warning",
                "Earth Search/STAC incompleto: no se podrán descargar bandas reales.",
            )
        )

    if not (0 <= cfg.earth_search.max_cloud_coverage <= 100):
        cfg.issues.append(
            ConfigIssue(
                "earth_search.max_cloud_coverage",
                "warning",
                "max_cloud_coverage fuera de [0,100]; se usará 100.",
            )
        )
        cfg.earth_search.max_cloud_coverage = 100

    if not cfg.earth_search.band_resolution_meters:
        cfg.issues.append(
            ConfigIssue(
                "earth_search.band_resolution_meters",
                "warning",
                "Resoluciones por banda vacías; se usará el mapa por defecto.",
            )
        )
        cfg.earth_search.band_resolution_meters = EarthSearchConfig().band_resolution_meters

    # Coherencia de processing.
    if cfg.processing.resolution_meters <= 0:
        cfg.issues.append(
            ConfigIssue("processing.resolution_meters", "warning", "Resolución inválida; se usará 10.")
        )
        cfg.processing.resolution_meters = 10

    if not (0 <= cfg.processing.max_production_cloud <= 100):
        cfg.issues.append(
            ConfigIssue(
                "processing.max_production_cloud",
                "warning",
                "max_production_cloud fuera de [0,100]; se usará 20.",
            )
        )
        cfg.processing.max_production_cloud = 20

    if not cfg.enabled:
        cfg.issues.append(
            ConfigIssue("enabled", "warning", "El servicio está marcado como enabled=false.")
        )
