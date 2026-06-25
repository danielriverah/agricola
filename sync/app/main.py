"""
AgroSentinel Sync Microservice — entrypoint FastAPI.
"""
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.core.errors import AppError
from app.core.logging_config import get_logger, setup_logging
from app.core.settings import get_settings
from app.routers import productions, phases, jobs_config, legacy, internal
from app.scheduler.runner import shutdown_scheduler, start_scheduler

setup_logging()
log = get_logger("agro-sync")


@asynccontextmanager
async def lifespan(app: FastAPI):
    s = get_settings()
    log.info("Iniciando AgroSentinel Sync | env=%s build=%s", s.APP_ENV, s.APP_BUILD_TAG)
    try:
        start_scheduler()
    except Exception as exc:  # noqa: BLE001
        log.warning("No se pudo iniciar el scheduler: %s", exc)
    yield
    shutdown_scheduler()
    log.info("AgroSentinel Sync detenido.")


app = FastAPI(
    title="AgroSentinel Sync Microservice",
    description="Comparar, sincronizar y validar datos de monitoreo entre "
                "DynamoDB, MySQL y S3. El servicio NO altera la estructura "
                "de la base de datos; dry_run=true solo simula.",
    version="1.0.0",
    lifespan=lifespan,
)


@app.exception_handler(AppError)
async def app_error_handler(_: Request, exc: AppError):
    return JSONResponse(status_code=exc.status_code, content={"error": exc.detail})


@app.get("/", tags=["health"])
def root():
    s = get_settings()
    return {
        "service": "agro-sync",
        "status": "ok",
        "env": s.APP_ENV,
        "build": s.APP_BUILD_TAG,
        "docs": "/docs",
    }


@app.get("/health", tags=["health"])
def health():
    from app.clients.mysql_client import get_mysql
    return {"status": "ok", "mysql": get_mysql().ping()}


# Routers
# IMPORTANTE: phases trae rutas LITERALES (/productions/escenes/*, /productions/ia/*)
# y debe registrarse ANTES que productions, cuyas rutas usan {production_id}.
# De lo contrario, "escenes" o "ia" se interpretarian como un production_id.
app.include_router(phases.router)
app.include_router(productions.router)
app.include_router(internal.router)
app.include_router(jobs_config.router)
app.include_router(legacy.router)
