import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import APP_BUILD_TAG, LOG_LEVEL
from app.api.routes import health, jobs, producciones, registros, sync


@asynccontextmanager
async def lifespan(app: FastAPI):
    logging.basicConfig(
        level=getattr(logging, LOG_LEVEL.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )
    logger = logging.getLogger("agro_sentinel_clima.startup")
    logger.info("APP_BUILD[agro-clima]: %s", APP_BUILD_TAG)
    logger.info("Scheduler interno deshabilitado: usar agro-schedule para ejecuciones programadas")
    yield


app = FastAPI(
    title="AgroSentinel Clima Service",
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def no_cache_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    response.headers["Surrogate-Control"] = "no-store"
    return response


app.include_router(health.router)
app.include_router(producciones.router)
app.include_router(registros.router)
app.include_router(sync.router)
app.include_router(jobs.router)


@app.get("/", include_in_schema=False)
def root():
    return {
        "service": app.title,
        "version": app.version,
        "docs": "/docs",
        "openapi": "/openapi.json",
        "redoc": "/redoc",
    }
