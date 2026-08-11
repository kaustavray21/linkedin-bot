from app.api.auth import router as auth_router
from app.api.discovery import router as discovery_router
from app.api.generate import router as generate_router
from app.api.health import router as health_router
from app.api.media import router as media_router
from app.api.posts import router as posts_router
from app.api.profile import router as profile_router
from app.api.scheduler import router as scheduler_router
from app.api.reference import router as reference_router

__all__ = [
    "auth_router",
    "discovery_router",
    "generate_router",
    "health_router",
    "media_router",
    "posts_router",
    "profile_router",
    "scheduler_router",
    "reference_router",
]

