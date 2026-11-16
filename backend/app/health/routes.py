from fastapi import APIRouter

router = APIRouter(prefix="/health")


@router.get("", include_in_schema=False)
def health_route():
    return "OK"
