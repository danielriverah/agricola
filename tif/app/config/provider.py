"""
Provider central de configuración runtime.

Responsabilidades:
  - leer app_config desde DynamoDB,
  - parsearlo de forma tolerante a AppConfig,
  - cachear con TTL,
  - exponer refresh manual (POST /internal/config/refresh),
  - sostener el modo degradado (nunca lanza al construirse).

Es el único punto desde el que el resto del servicio obtiene configuración.
"""

from __future__ import annotations

import logging
import threading
import time

from app.config.dynamo_reader import DynamoConfigReader
from app.config.env_settings import EnvSettings, get_env_settings
from app.config.models import AppConfig
from app.config.validator import parse_app_config

logger = logging.getLogger("tif.config")


class ConfigProvider:
    def __init__(self, env: EnvSettings | None = None) -> None:
        self.env = env or get_env_settings()
        self.reader = DynamoConfigReader(self.env)
        self._lock = threading.Lock()
        self._config: AppConfig | None = None
        self._loaded_at: float = 0.0

    # ---------------- carga / cache ----------------

    def _load(self) -> AppConfig:
        raw = self.reader.read_item()
        cfg = parse_app_config(raw)
        self._config = cfg
        self._loaded_at = time.time()
        logger.info(
            "app_config cargado: status=%s errores=%d warnings=%d",
            cfg.status_label(),
            len(cfg.errors()),
            len(cfg.warnings()),
        )
        return cfg

    def _is_stale(self) -> bool:
        if self._config is None:
            return True
        ttl = max(0, self.env.config_cache_ttl_seconds)
        if ttl == 0:
            return True
        return (time.time() - self._loaded_at) > ttl

    def get(self) -> AppConfig:
        with self._lock:
            if self._is_stale():
                self._load()
            return self._config  # type: ignore[return-value]

    def refresh(self) -> AppConfig:
        with self._lock:
            return self._load()

    # ---------------- arranque ----------------

    def initialize(self) -> AppConfig:
        """Llamado en startup. Respeta CONFIG_FAIL_FAST."""
        cfg = self.refresh()
        if self.env.config_fail_fast and not cfg.is_ready():
            raise RuntimeError(
                "CONFIG_FAIL_FAST=true y la configuración no está lista: "
                + "; ".join(i.message for i in cfg.errors())
            )
        return cfg


# Singleton de proceso.
_provider: ConfigProvider | None = None
_provider_lock = threading.Lock()


def get_config_provider() -> ConfigProvider:
    global _provider
    if _provider is None:
        with _provider_lock:
            if _provider is None:
                _provider = ConfigProvider()
    return _provider


def get_config() -> AppConfig:
    """Atajo usado por dependencias FastAPI y servicios."""
    return get_config_provider().get()
