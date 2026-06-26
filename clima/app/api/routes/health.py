import time
from fastapi import APIRouter
from app.core.config import APP_BUILD_TAG, SCHEDULER_ENABLED, SCHEDULER_TIME, SCHEDULER_TIMEZONE

router = APIRouter(tags=["health"])

_start_time = time.time()


@router.get("/health")
def health():
    return {
        "status": "ok",
        "service": "agro-sentinel-clima",
        "version": "0.1.0",
        "build": APP_BUILD_TAG,
        "uptime_seconds": int(time.time() - _start_time),
        "scheduler": {
            "enabled": SCHEDULER_ENABLED,
            "time": SCHEDULER_TIME if SCHEDULER_ENABLED else None,
            "timezone": SCHEDULER_TIMEZONE if SCHEDULER_ENABLED else None,
        },
    }
