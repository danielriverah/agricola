from __future__ import annotations

from fastapi import APIRouter, HTTPException, Path

from app.jobs.manager import get_job_manager

router = APIRouter(tags=["jobs"])


@router.get("/jobs", summary="Listar jobs de clima")
def list_jobs():
    return {"items": [j.as_dict() for j in get_job_manager().list()]}


@router.get("/jobs/active", summary="Job pesado activo y jobs pending/running")
def active_job():
    jm = get_job_manager()
    active_heavy = jm.active_heavy()
    return {
        "active_heavy": active_heavy.as_dict() if active_heavy else None,
        "items": [j.as_dict() for j in jm.active()],
    }


@router.get("/jobs/{job_id}", summary="Estado y progreso de un job")
def job_status(job_id: str = Path(...)):
    job = get_job_manager().get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job no encontrado: {job_id}")
    return job.as_dict()


@router.post("/jobs/cancel/{job_id}", summary="Cancelar un job en ejecución")
def cancel_job(job_id: str = Path(...)):
    jm = get_job_manager()
    if not jm.get(job_id):
        raise HTTPException(status_code=404, detail=f"Job no encontrado: {job_id}")
    cancelled = jm.cancel(job_id)
    return {"job_id": job_id, "cancel_requested": cancelled}
