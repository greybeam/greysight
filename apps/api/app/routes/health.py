from fastapi import APIRouter

router = APIRouter()


@router.get("/health")
def read_health() -> dict[str, str]:
    return {"status": "ok", "service": "greysight-api"}
