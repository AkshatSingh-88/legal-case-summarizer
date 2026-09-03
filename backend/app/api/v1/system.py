"""V1 System router skeleton."""

from datetime import datetime, timezone
from fastapi import APIRouter

from backend.app.api.schemas.system import SystemHealthResponse
from backend.app.config import get_settings

router = APIRouter(prefix="/system", tags=["System"])


@router.get("/health", response_model=SystemHealthResponse, summary="System Health Check")
def get_system_health() -> SystemHealthResponse:
    """Returns basic system health, environment, and version information."""
    settings = get_settings()
    return SystemHealthResponse(
        status="ok",
        version="0.1.0",
        environment=settings.env,
        timestamp=datetime.now(timezone.utc),
    )
