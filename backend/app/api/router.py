from fastapi import APIRouter

from backend.app.api.health import router as health_router
from backend.app.api.v1.router import v1_router

api_router = APIRouter()

api_router.include_router(health_router, tags=["health"])
api_router.include_router(v1_router)

