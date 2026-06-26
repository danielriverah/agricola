from contextlib import asynccontextmanager

from fastapi import FastAPI, Request

from app.api.routes import alerts, analyze, health, internal, jobs, lots, webhook


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield


app = FastAPI(
    title="AgroSentinel IA Service",
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
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
app.include_router(analyze.router)
app.include_router(jobs.router)
app.include_router(lots.router)
app.include_router(alerts.router)
app.include_router(webhook.router)
app.include_router(internal.router)


@app.get("/", include_in_schema=False)
def root():
    return {
        "service": app.title,
        "version": app.version,
        "docs": "/docs",
        "openapi": "/openapi.json",
        "redoc": "/redoc",
    }
