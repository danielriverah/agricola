from fastapi import APIRouter

router = APIRouter(tags=["webhook"])


@router.post("/webhook/retry/{job_id}")
async def retry_webhook(job_id: str):
    return {"status": "not_implemented", "job_id": job_id}
