from fastapi import APIRouter

router = APIRouter()


@router.get("/health")
def health_check():
    return {"status": "ok"}


@router.get("/healthz")
def health_check_alias():
    return {"status": "ok"}
