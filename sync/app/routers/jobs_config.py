from fastapi import APIRouter, Query

from app.services.config_service import get_runtime_config, refresh_runtime_config
from app.services.job_manager import get_job_manager

router = APIRouter(tags=["jobs-config"])
jobs = get_job_manager()


# ===================== JOBS =====================
@router.get("/sync/jobs")
def list_jobs():
    return {"jobs": jobs.list_jobs()}


@router.get("/sync/jobs/active")
def active_job():
    job = jobs.active_job()
    return {"active": job.to_dict() if job else None}


@router.get("/sync/jobs/{job_id}")
def get_job(job_id: str):
    return jobs.get_job(job_id)


@router.post("/sync/jobs/cancel/{job_id}")
def cancel_job(job_id: str):
    return jobs.request_cancel(job_id)


# ===================== CONFIG RUNTIME =====================
@router.get("/config/runtime")
def config_runtime(force_refresh: bool = Query(False)):
    cfg = get_runtime_config(force_refresh=force_refresh)
    return cfg.safe_dict()


@router.post("/config/runtime/refresh")
def config_refresh():
    cfg = refresh_runtime_config()
    return {"status": "refreshed", "config": cfg.safe_dict()}
