"""Router de jobs bajo demanda (sin scheduler)."""

from __future__ import annotations

from fastapi import APIRouter, Path

from app.core.errors import ValidationError
from app.jobs.manager import get_job_manager

router = APIRouter(tags=["jobs"])


@router.get("/jobs", summary="Listar jobs de generación de tif")
@router.get("/monitoring/scenes/missing-tif/jobs", include_in_schema=False)
def list_jobs():
    return {"items": [j.as_dict() for j in get_job_manager().list()]}


@router.get("/jobs/active", summary="Job pesado activo y jobs pending/running")
@router.get("/monitoring/scenes/missing-tif/active-job", include_in_schema=False)
def active_job():
    jm = get_job_manager()
    active_heavy = jm.active_heavy()
    return {
        "active_heavy": active_heavy.as_dict() if active_heavy else None,
        "items": [j.as_dict() for j in jm.active()],
    }


@router.get("/jobs/{job_id}", summary="Estado y progreso de un job")
@router.get("/monitoring/scenes/missing-tif/status/{job_id}", include_in_schema=False)
def job_status(job_id: str = Path(...)):
    job = get_job_manager().get(job_id)
    if not job:
        raise ValidationError(f"Job no encontrado: {job_id}", "job_id")
    return job.as_dict()


@router.post("/jobs/cancel/{job_id}", summary="Cancelar un job en ejecución")
@router.post("/monitoring/scenes/missing-tif/cancel/{job_id}", include_in_schema=False)
def cancel_job(job_id: str = Path(...)):
    jm = get_job_manager()
    if not jm.get(job_id):
        raise ValidationError(f"Job no encontrado: {job_id}", "job_id")
    cancelled = jm.cancel(job_id)
    return {"job_id": job_id, "cancel_requested": cancelled}
