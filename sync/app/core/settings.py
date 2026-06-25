"""
Settings base del microservicio.

Principio del README:
  - El .env SOLO deja la conexion a DynamoDB y el identificador del
    registro de configuracion (APP_CONFIG_*).
  - El resto de parametros operativos (MySQL, S3, scheduler) viven en
    el item de configuracion centralizada en DynamoDB y se leen en runtime.
"""
from functools import lru_cache
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Meta ---
    APP_ENV: str = "local"
    APP_BUILD_TAG: str = "dev"
    LOG_LEVEL: str = "INFO"
    SERVICE_PORT: int = 8006

    # --- Conexion DynamoDB ---
    AWS_REGION: str = "us-east-1"
    DYNAMODB_USE_AWS: bool = False
    DYNAMODB_ENDPOINT_URL: Optional[str] = None
    AWS_ACCESS_KEY_ID_CUSTOM: Optional[str] = None
    AWS_SECRET_ACCESS_KEY_CUSTOM: Optional[str] = None
    AWS_SESSION_TOKEN_CUSTOM: Optional[str] = None

    # --- Tablas base de Dynamo (identificadores de configuracion) ---
    PRODUCTION_MONITORING_TABLE_NAME: str = "production_monitoring"
    PRODUCTION_MONITORING_DETAIL_TABLE_NAME: str = "production_monitoring_detalle"
    PRODUCTION_MONITORING_PK: str = "produccion_id"
    PRODUCTION_MONITORING_SK: Optional[str] = None
    PRODUCTION_MONITORING_SK_VALUE_TEMPLATE: Optional[str] = None

    # --- Item de configuracion centralizada ---
    APP_CONFIG_TABLE_NAME: str = "app_config"
    APP_CONFIG_ITEM_ID: str = "microservicio-sync"
    APP_CONFIG_ITEM_PK: str = "config_id"
    APP_CONFIG_ITEM_SK: Optional[str] = None  # reservado para extensiones futuras
    APP_CONFIG_ITEM_SK_VALUE: Optional[str] = None

    # --- Fallbacks opcionales si el item de config no esta disponible ---
    # (permiten arrancar el servicio aunque Dynamo config no responda)
    MYSQL_HOST: Optional[str] = None
    MYSQL_PORT: int = 3306
    MYSQL_DATABASE: Optional[str] = None
    MYSQL_USER: Optional[str] = None
    MYSQL_PASSWORD: Optional[str] = None
    MYSQL_TARGET_TABLE: str = "s3_monitoring_producciones"
    MYSQL_SCENES_TABLE: str = "s3_monitoring_escenas"
    MYSQL_SCENE_FILES_TABLE: str = "s3_monitoring_escena_archivos"
    MYSQL_SCENE_IA_TABLE: str = "s3_monitoring_escena_ia_resumen"
    MYSQL_DAEMON_LOGS_TABLE: str = "monitoring_daemon_logs"

    MONITORING_S3_BUCKET: Optional[str] = None
    MONITORING_S3_PREFIX: str = "previews"
    MONITORING_S3_PHASE2_FILES_TEMPLATE: str = "previews/PROD_{production_id}/{scene_name}"
    MONITORING_S3_PHASE2_FILES: Optional[str] = None  # compatibilidad con el nombre anterior
    MONITORING_S3_PHASE2_EXPECTED_FILES: str = (
        "multiband.tif,multiband.params.json,multiband.ia.json,"
        "evi.png,false_color_veg.png,gndvi.png,natural.png,nbr.png,ndmi.png,"
        "ndre.png,ndvi.png,red_edge.png,savi.png,swir.png"
    )
    SYNC_PREFIX_TEMPLATE: str = "previews/PROD_{production_id}"


@lru_cache
def get_settings() -> Settings:
    return Settings()
