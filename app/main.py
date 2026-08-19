from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import os

from app.api import (
    auth_router,
    discovery_router,
    generate_router,
    health_router,
    media_router,
    post_types_router,
    posts_router,
    profile_router,
    scheduler_router,
)
from app.api.deps import set_scheduler_service
from app.core.config import settings
from app.core.exceptions import AppException
from app.core.logger import get_logger, setup_logging
from app.core.security import mask_secret
from app.services.scheduler_service import SchedulerService
from app.utils.helpers import generate_request_id

log = get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    log.info("Starting LinkedIn Bot application")
    log.info(
        "Configuration loaded",
        mysql_host=settings.mysql_host,
        mysql_database=settings.mysql_database,
        client_id=mask_secret(settings.client_id),
        client_secret=mask_secret(settings.client_secret) if settings.client_secret else "NOT_SET",
        redirect_uri=settings.redirect_uri,
        log_level=settings.log_level,
    )

    scheduler_service = SchedulerService()
    scheduler_service.start()
    set_scheduler_service(scheduler_service)

    yield

    scheduler_service.stop()
    log.info("Application shutdown complete")


app = FastAPI(
    title="LinkedIn Auto Posting Bot",
    description="Production-ready LinkedIn auto-posting application with OAuth 2.0, scheduling, and AI-generated content",
    version="1.0.0",
    lifespan=lifespan,
)


@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    request_id = generate_request_id()
    log = get_logger(request_id=request_id)
    log.info(f"{request.method} {request.url.path}")

    response: Response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    return response


@app.exception_handler(AppException)
async def app_exception_handler(request: Request, exc: AppException):
    log.error(
        "Application exception",
        status_code=exc.status_code,
        detail=exc.detail,
        path=request.url.path,
    )
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail},
    )


app.include_router(health_router)
app.include_router(auth_router)
app.include_router(profile_router)
app.include_router(posts_router)
app.include_router(scheduler_router)
app.include_router(generate_router)
app.include_router(media_router)
app.include_router(discovery_router)
app.include_router(post_types_router)


# Ensure uploads and static directories exist
os.makedirs(settings.uploads_dir, exist_ok=True)
os.makedirs("app/static", exist_ok=True)

# Mount static files
app.mount("/static", StaticFiles(directory="app/static"), name="static")


@app.get("/")
async def serve_index():
    return FileResponse("app/static/index.html")

