"""V1 API Router aggregating all v1 sub-routers."""

from fastapi import APIRouter

from backend.app.api.v1.auth import router as auth_router
from backend.app.api.v1.cases import router as cases_router
from backend.app.api.v1.documents import router as documents_router
from backend.app.api.v1.guest import router as guest_router
from backend.app.api.v1.jobs import router as jobs_router
from backend.app.api.v1.summaries import router as summaries_router
from backend.app.api.v1.system import router as system_router

v1_router = APIRouter(prefix="/v1")

v1_router.include_router(system_router)
v1_router.include_router(auth_router)
v1_router.include_router(cases_router)
v1_router.include_router(documents_router)
v1_router.include_router(jobs_router)
v1_router.include_router(summaries_router)
v1_router.include_router(guest_router)
