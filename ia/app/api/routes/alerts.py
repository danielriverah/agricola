from fastapi import APIRouter

router = APIRouter(tags=["alerts"])


@router.get("/alerts")
async def get_alerts():
    return {"status": "not_implemented", "alerts": []}
