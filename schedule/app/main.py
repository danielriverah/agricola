"""Punto de entrada del microservicio schedule."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes import router
from app.core.app_config import load_app_config
from app.core.runtime import (
    add_bootstrap_error,
    clear_bootstrap_errors,
    has_app_config,
    has_mysql,
    set_app_config,
    set_mysql_ready,
)
from app.core.settings import get_settings
from app.db import mysql
from app.services.worker import worker

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s | %(message)s",
)
logger = logging.getLogger("schedule")

STATIC_DIR = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    if not settings.skip_bootstrap:
        clear_bootstrap_errors()
        cfg = None
        try:
            logger.info("Cargando app_config desde DynamoDB...")
            cfg = load_app_config(settings)
            set_app_config(cfg)
        except Exception as exc:  # noqa: BLE001
            message = f"No se pudo cargar app_config: {exc}"
            logger.exception(message)
            add_bootstrap_error(message)

        if cfg is not None:
            try:
                logger.info("Inicializando MySQL...")
                mysql.init_pool(cfg.mysql)
                set_mysql_ready(True)
            except Exception as exc:  # noqa: BLE001
                message = f"No se pudo inicializar MySQL: {exc}"
                logger.exception(message)
                add_bootstrap_error(message)
                set_mysql_ready(False)

        if cfg is not None and settings.scheduler_enabled and cfg.enabled and has_app_config() and has_mysql():
            try:
                worker.start()
            except Exception as exc:  # noqa: BLE001
                message = f"No se pudo iniciar el worker del scheduler: {exc}"
                logger.exception(message)
                add_bootstrap_error(message)
        else:
            logger.info("Scheduler worker NO iniciado.")
    else:
        logger.warning("SKIP_BOOTSTRAP=true: arrancando sin DynamoDB/MySQL.")

    yield

    if not settings.skip_bootstrap and settings.scheduler_enabled:
        await worker.stop()


app = FastAPI(
    title="Microservicio schedule",
    description="Programa y dispara ejecuciones HTTP contra otros microservicios.",
    version="1.0.0",
    lifespan=lifespan,
)

app.include_router(router)

if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(str(STATIC_DIR / "index.html"))
