"""
Configuracion centralizada en DynamoDB.

Lee un item especifico de la tabla APP_CONFIG_TABLE_NAME y expone sus valores
operativos (MySQL, S3, scheduler) en runtime, con cache en memoria.

  - GET  /config/runtime          -> ver lo que el servicio esta leyendo
  - GET  /config/runtime?force_refresh=true
  - POST /config/runtime/refresh  -> fuerza refresco inmediato

Los cambios de operacion NO requieren reiniciar uvicorn.
"""
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from app.core.logging_config import get_logger
from app.core.settings import get_settings

log = get_logger(__name__)

_CACHE_TTL_SECONDS = 60


@dataclass
class RuntimeConfig:
    # MySQL
    mysql_host: Optional[str] = None
    mysql_port: int = 3306
    mysql_database: Optional[str] = None
    mysql_user: Optional[str] = None
    mysql_password: Optional[str] = None
    mysql_target_table: str = "s3_monitoring_producciones"
    mysql_scenes_table: str = "s3_monitoring_escenas"
    mysql_scene_files_table: str = "s3_monitoring_escena_archivos"
    mysql_scene_ia_table: str = "s3_monitoring_escena_ia_resumen"
    mysql_daemon_logs_table: str = "monitoring_daemon_logs"

    # S3
    s3_bucket: Optional[str] = None
    s3_prefix: str = "previews"
    s3_phase2_files_template: str = "previews/PROD_{production_id}/{scene_name}"
    sync_prefix_template: str = "previews/PROD_{production_id}"
    s3_phase2_expected_files: list[str] = field(default_factory=list)

    # Scheduler
    scheduler_enabled: bool = False
    scheduler_mode: str = "daily"  # interval | daily | every_n_days
    scheduler_time: str = "02:00"
    scheduler_timezone: str = "America/Mexico_City"
    scheduler_interval_seconds: int = 3600
    scheduler_every_n_days: int = 1
    scheduler_dry_run: bool = True
    scheduler_active_only: bool = False
    scheduler_batch_size: int = 20
    scheduler_include_scene_json: bool = False
    scheduler_date_from: Optional[str] = None
    scheduler_date_to: Optional[str] = None
    scheduler_service_name: str = "schedule_micro_serv_sync"

    # meta
    loaded_at: float = field(default_factory=time.time)
    source: str = "defaults"

    def safe_dict(self) -> dict[str, Any]:
        """Igual que asdict pero enmascara el password."""
        d = self.__dict__.copy()
        if d.get("mysql_password"):
            d["mysql_password"] = "***"
        return d


class ConfigService:
    def __init__(self):
        self._lock = threading.Lock()
        self._config: Optional[RuntimeConfig] = None
        self._loaded_at: float = 0.0

    def _read_item(self) -> Optional[dict]:
        """Lee el item de config desde Dynamo. Import diferido para evitar ciclos."""
        from app.clients.dynamo_client import get_dynamo

        s = get_settings()
        key = {s.APP_CONFIG_ITEM_PK: s.APP_CONFIG_ITEM_ID}
        if s.APP_CONFIG_ITEM_SK:
            # solo si esta definido (reservado para extensiones futuras)
            key[s.APP_CONFIG_ITEM_SK] = s.APP_CONFIG_ITEM_SK_VALUE or "runtime"
        try:
            result=get_dynamo().get_item(s.APP_CONFIG_TABLE_NAME, key)
            print("consulta Realizada en dynamo")
            return result
        except Exception as exc:  # noqa: BLE001
            print("errror al consultar dYnamoDB")
            print(exc)
            log.warning("No se pudo leer item de config en Dynamo: %s", exc)
            return None

    def _build(self, item: Optional[dict]) -> RuntimeConfig:
        s = get_settings()
        item = item or {}

        def pick(key: str, fallback: Any) -> Any:
            val = item.get(key)
            return val if val is not None else fallback

        cfg = RuntimeConfig(
            mysql_host=pick("MYSQL_HOST", s.MYSQL_HOST),
            mysql_port=int(pick("MYSQL_PORT", s.MYSQL_PORT)),
            mysql_database=pick("MYSQL_DATABASE", s.MYSQL_DATABASE),
            mysql_user=pick("MYSQL_USER", s.MYSQL_USER),
            mysql_password=pick("MYSQL_PASSWORD", s.MYSQL_PASSWORD),
            mysql_target_table=pick("MYSQL_TARGET_TABLE", s.MYSQL_TARGET_TABLE),
            mysql_scenes_table=pick("MYSQL_SCENES_TABLE", s.MYSQL_SCENES_TABLE),
            mysql_scene_files_table=pick("MYSQL_SCENE_FILES_TABLE", s.MYSQL_SCENE_FILES_TABLE),
            mysql_scene_ia_table=pick("MYSQL_SCENE_IA_TABLE", s.MYSQL_SCENE_IA_TABLE),
            mysql_daemon_logs_table=pick("MYSQL_DAEMON_LOGS_TABLE", s.MYSQL_DAEMON_LOGS_TABLE),
            s3_bucket=pick("MONITORING_S3_BUCKET", s.MONITORING_S3_BUCKET),
            s3_prefix=pick("MONITORING_S3_PREFIX", s.MONITORING_S3_PREFIX),
            s3_phase2_files_template=pick(
                "MONITORING_S3_PHASE2_FILES_TEMPLATE",
                pick("MONITORING_S3_PHASE2_FILES", s.MONITORING_S3_PHASE2_FILES)
                or s.MONITORING_S3_PHASE2_FILES_TEMPLATE,
            ),
            sync_prefix_template=pick("SYNC_PREFIX_TEMPLATE", s.SYNC_PREFIX_TEMPLATE),
            s3_phase2_expected_files=[
                item.strip() for item in pick(
                    "MONITORING_S3_PHASE2_EXPECTED_FILES",
                    s.MONITORING_S3_PHASE2_EXPECTED_FILES,
                ).split(",") if item.strip()
            ],
            scheduler_enabled=bool(pick("SYNC_SCHEDULER_ENABLED", False)),
            scheduler_mode=pick("SYNC_SCHEDULER_MODE", "daily"),
            scheduler_time=pick("SYNC_SCHEDULER_TIME", "02:00"),
            scheduler_timezone=pick("SYNC_SCHEDULER_TIMEZONE", "America/Mexico_City"),
            scheduler_interval_seconds=int(pick("SYNC_SCHEDULER_INTERVAL_SECONDS", 3600)),
            scheduler_every_n_days=int(pick("SYNC_SCHEDULER_EVERY_N_DAYS", 1)),
            scheduler_dry_run=bool(pick("SYNC_SCHEDULER_DRY_RUN", True)),
            scheduler_active_only=bool(pick("SYNC_SCHEDULER_ACTIVE_ONLY", False)),
            scheduler_batch_size=int(pick("SYNC_SCHEDULER_BATCH_SIZE", 20)),
            scheduler_include_scene_json=bool(pick("SYNC_SCHEDULER_INCLUDE_SCENE_JSON", False)),
            scheduler_date_from=pick("SYNC_SCHEDULER_DATE_FROM", None),
            scheduler_date_to=pick("SYNC_SCHEDULER_DATE_TO", None),
            scheduler_service_name=pick("SYNC_SCHEDULER_SERVICE_NAME", "schedule_micro_serv_sync"),
            source="dynamodb" if item else "defaults/env",
        )
        return cfg

    def get(self, force_refresh: bool = False) -> RuntimeConfig:
        with self._lock:
            expired = (time.time() - self._loaded_at) > _CACHE_TTL_SECONDS
            if force_refresh or self._config is None or expired:
                item = self._read_item()
                self._config = self._build(item)
                self._loaded_at = time.time()
                log.info("Config runtime cargada | source=%s", self._config.source)
            return self._config

    def refresh(self) -> RuntimeConfig:
        return self.get(force_refresh=True)


_service: Optional[ConfigService] = None


def _svc() -> ConfigService:
    global _service
    if _service is None:
        _service = ConfigService()
    return _service


def get_runtime_config(force_refresh: bool = False) -> RuntimeConfig:
    return _svc().get(force_refresh=force_refresh)


def refresh_runtime_config() -> RuntimeConfig:
    return _svc().refresh()
