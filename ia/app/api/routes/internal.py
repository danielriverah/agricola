from fastapi import APIRouter

router = APIRouter(prefix="/internal", tags=["internal"])


@router.get("/config/validate")
async def config_validate():
    return {"status": "not_implemented"}


@router.post("/config/refresh")
async def config_refresh():
    return {"status": "not_implemented"}
