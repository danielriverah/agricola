from fastapi import APIRouter

router = APIRouter(tags=["jobs"])


@router.get("/jobs/{job_id}/status")
async def get_job_status(job_id: str):
    return {"status": "not_implemented", "job_id": job_id}
