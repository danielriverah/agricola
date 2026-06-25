"""
Entrypoint del microservicio TIF.

Arranque:
  uvicorn app.main:app --host 0.0.0.0 --port 5300 --reload   (desarrollo)
  uvicorn app.main:app --host 0.0.0.0 --port 5300            (producción)

El servicio NUNCA debe romperse al iniciar por falta de configuración. En su
lugar reporta el estado degradado por /health, /config/view y
/internal/config/validate. Solo se detiene al iniciar si CONFIG_FAIL_FAST=true.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from app.config.provider import get_config_provider
from app.routers import admin, health, ia_handoff, jobs, monitoring, tif

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("tif.main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    provider = get_config_provider()
    try:
        cfg = provider.initialize()
        logger.info("TIF iniciado. Estado de configuración: %s", cfg.status_label())
        if not cfg.is_ready():
            logger.warning(
                "Configuración incompleta (modo degradado). Errores: %s",
                [i.message for i in cfg.errors()],
            )
    except RuntimeError as exc:
        # Solo ocurre con CONFIG_FAIL_FAST=true.
        logger.error("Arranque abortado por CONFIG_FAIL_FAST: %s", exc)
        raise
    yield
    logger.info("TIF detenido.")


app = FastAPI(
    title="AgroSentinel TIF Microservice",
    version="1.0.0",
    description=(
        "Microservicio REST de SOLO LECTURA hacia bases de datos. Genera productos "
        "raster (multiband.tif y derivados) a partir de escenas indexadas en MySQL. "
        "DynamoDB se usa solo para configuración runtime (app_config). No ejecuta IA, "
        "no escribe en MySQL/DynamoDB y no programa tareas recurrentes."
    ),
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
)

# Routers. El orden importa: las rutas ESTÁTICAS deben registrarse antes que
# las que tienen parámetros de path.
app.include_router(health.router)
app.include_router(jobs.router)       # /jobs, /jobs/active, /jobs/{job_id}, /jobs/cancel/{job_id}
app.include_router(tif.router)        # incluye /monitoring/scenes/missing-tif/{production_id}
app.include_router(monitoring.router)  # incluye /monitoring/scenes/{production_id}
app.include_router(ia_handoff.router)
app.include_router(admin.router)


@app.get("/", include_in_schema=False)
def root():
    return JSONResponse(
        {
            "service": "agro-tif",
            "docs": "/docs",
            "openapi": "/openapi.json",
            "health": "/health",
            "db_writes": False,
        }
    )
