"""
Modelo de la configuración runtime que vive en DynamoDB -> app_config.

Punto clave del README:
    El servicio debe arrancar de forma DEGRADADA cuando falte configuración no
    crítica y reportar claramente qué falta. No debe fallar silenciosamente ni
    romper Swagger con trazas crudas.

Por eso este módulo NO usa validación estricta de Pydantic que lance excepción.
En su lugar:
  - parsea de forma tolerante lo que venga del item DynamoDB,
  - rellena defaults seguros,
  - y produce una lista de `issues` (faltantes / inválidos) que alimenta
    /health y /internal/config/validate.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# ---------------------------------------------------------------------------
# Estructura de issues de configuración
# ---------------------------------------------------------------------------

@dataclass
class ConfigIssue:
    field: str
    severity: str  # "error" (crítico para esa capa) | "warning"
    message: str

    def as_dict(self) -> dict:
        return {"field": self.field, "severity": self.severity, "message": self.message}


# ---------------------------------------------------------------------------
# Sub-secciones
# ---------------------------------------------------------------------------

@dataclass
class MySQLConfig:
    host: str | None = None
    port: int = 3306
    database: str | None = None
    user: str | None = None
    password: str | None = None
    strict_mode: bool = True

    def is_usable(self) -> bool:
        return bool(self.host and self.database and self.user is not None)

    def safe_dict(self) -> dict:
        return {
            "host": self.host,
            "port": self.port,
            "database": self.database,
            "user": self.user,
            "password": "***" if self.password else None,
            "strict_mode": self.strict_mode,
        }


@dataclass
class StorageConfig:
    driver: str = "s3"
    s3_bucket: str | None = None
    base_path: str = "previews/PROD_{production_id}/{scene_name}"
    public_url_ttl_minutes: int = 60

    def is_usable(self) -> bool:
        if self.driver == "s3":
            return bool(self.s3_bucket)
        return True  # driver local u otro no exige bucket

    def safe_dict(self) -> dict:
        return {
            "driver": self.driver,
            "s3_bucket": self.s3_bucket,
            "base_path": self.base_path,
            "public_url_ttl_minutes": self.public_url_ttl_minutes,
        }


@dataclass
class EarthSearchConfig:
    search_url: str = "https://earth-search.aws.element84.com/v1/search"
    collection: str = "sentinel-2-l2a"
    max_cloud_coverage: int = 100
    request_timeout_seconds: int = 120
    band_resolution_meters: dict[str, int] = field(
        default_factory=lambda: {
            "B02": 10,
            "B03": 10,
            "B04": 10,
            "B08": 10,
            "B05": 20,
            "B06": 20,
            "B07": 20,
            "B8A": 20,
            "B11": 20,
            "B12": 20,
            "SCL": 20,
        }
    )
    asset_map: dict[str, str] = field(
        default_factory=lambda: {
            "B02": "blue",
            "B03": "green",
            "B04": "red",
            "B05": "rededge1",
            "B06": "rededge2",
            "B07": "rededge3",
            "B08": "nir",
            "B8A": "nir08",
            "B11": "swir16",
            "B12": "swir22",
            "SCL": "scl",
        }
    )

    def is_usable(self) -> bool:
        return bool(self.search_url and self.collection)

    def safe_dict(self) -> dict:
        return {
            "search_url": self.search_url,
            "collection": self.collection,
            "max_cloud_coverage": self.max_cloud_coverage,
            "request_timeout_seconds": self.request_timeout_seconds,
            "band_resolution_meters": self.band_resolution_meters,
            "asset_map": self.asset_map,
        }


@dataclass
class ProcessingConfig:
    default_indices: list[str] = field(
        default_factory=lambda: [
            "evi", "false_color_veg", "gndvi", "natural", "nbr", "ndmi",
            "ndre", "ndvi", "red_edge", "savi", "swir",
        ]
    )
    resolution_meters: int = 10
    apply_cloud_mask: bool = True
    max_production_cloud: float = 20
    min_valid_pixels_percentage: float = 1
    generate_png: bool = True
    generate_geotiff: bool = True
    generate_pdf: bool = False

    def safe_dict(self) -> dict:
        return {
            "default_indices": self.default_indices,
            "resolution_meters": self.resolution_meters,
            "apply_cloud_mask": self.apply_cloud_mask,
            "max_production_cloud": self.max_production_cloud,
            "min_valid_pixels_percentage": self.min_valid_pixels_percentage,
            "generate_png": self.generate_png,
            "generate_geotiff": self.generate_geotiff,
            "generate_pdf": self.generate_pdf,
        }


@dataclass
class OutputsConfig:
    multiband_filename: str = "multiband.tif"
    params_filename: str = "multiband.params.json"
    temp_prune_on_scene_select: bool = True
    temp_clean_expired_on_scene_select: bool = True
    temp_expire_ttl_hours: int = 24

    def safe_dict(self) -> dict:
        return self.__dict__.copy()


@dataclass
class SecurityConfig:
    api_secret_key: str | None = None

    def safe_dict(self) -> dict:
        return {"api_secret_key": "***" if self.api_secret_key else None}


# ---------------------------------------------------------------------------
# Configuración completa
# ---------------------------------------------------------------------------

@dataclass
class AppConfig:
    raw_present: bool = False  # ¿se encontró el item en DynamoDB?
    config_id: str | None = None
    enabled: bool = True
    timezone: str = "America/Mexico_City"
    request_timeout_seconds: int = 60

    mysql: MySQLConfig = field(default_factory=MySQLConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)
    earth_search: EarthSearchConfig = field(default_factory=EarthSearchConfig)
    processing: ProcessingConfig = field(default_factory=ProcessingConfig)
    outputs: OutputsConfig = field(default_factory=OutputsConfig)
    targets: dict = field(default_factory=dict)
    security: SecurityConfig = field(default_factory=SecurityConfig)

    # Resultado de la validación.
    issues: list[ConfigIssue] = field(default_factory=list)

    # ---------------- Helpers de estado -----------------

    def errors(self) -> list[ConfigIssue]:
        return [i for i in self.issues if i.severity == "error"]

    def warnings(self) -> list[ConfigIssue]:
        return [i for i in self.issues if i.severity == "warning"]

    def is_ready(self) -> bool:
        """Listo = sin errores críticos de configuración mínima."""
        return self.raw_present and not self.errors()

    def status_label(self) -> str:
        if not self.raw_present:
            return "degraded"
        if self.errors():
            return "degraded"
        if self.warnings():
            return "ok_with_warnings"
        return "ok"

    def safe_dict(self) -> dict:
        """Para /config/view: configuración cargada SIN secretos."""
        return {
            "raw_present": self.raw_present,
            "config_id": self.config_id,
            "enabled": self.enabled,
            "timezone": self.timezone,
            "request_timeout_seconds": self.request_timeout_seconds,
            "mysql": self.mysql.safe_dict(),
            "storage": self.storage.safe_dict(),
            "earth_search": self.earth_search.safe_dict(),
            "processing": self.processing.safe_dict(),
            "outputs": self.outputs.safe_dict(),
            "targets": self.targets,
            "security": self.security.safe_dict(),
        }
