import logging

from fastapi import FastAPI

from backend.app.api.router import api_router
from backend.app.config import get_settings
from backend.app.logging_config import configure_logging

logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)

    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
    )

    app.include_router(api_router, prefix=settings.api_prefix)

    @app.get("/", tags=["root"])
    def root():
        return {
            "name": settings.app_name,
            "status": "running",
            "env": settings.env,
        }

    logger.info("Application initialized (env=%s)", settings.env)
    return app


app = create_app()
