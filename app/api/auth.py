from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppException
from app.database.connection import get_session
from app.schemas.auth import AuthUrlResponse, CallbackResponse, MeResponse
from app.services.oauth_service import OAuthService
from app.core.logger import get_logger

log = get_logger(tag="oauth")

router = APIRouter(prefix="/auth", tags=["auth"])

# In-memory OAuth state store (use Redis in production)
_state_store: dict[str, bool] = {}


def get_state_store() -> dict[str, bool]:
    return _state_store


@router.get("/login", response_model=AuthUrlResponse)
async def login(db: AsyncSession = Depends(get_session)):
    oauth_service = OAuthService(db)
    auth_url, state = oauth_service.get_authorization_url()
    _state_store[state] = True
    log.debug("Login initiated")
    return AuthUrlResponse(auth_url=auth_url)


from fastapi.responses import RedirectResponse


@router.get("/callback")
async def callback(
    code: str | None = Query(None),
    state: str | None = Query(None),
    error: str | None = Query(None),
    error_description: str | None = Query(None),
    db: AsyncSession = Depends(get_session),
):
    if error:
        log.error("LinkedIn OAuth error received", error=error, description=error_description)
        return RedirectResponse(url=f"/?error={error_description or error}")

    if not code or not state:
        return RedirectResponse(url="/?error=Missing+authorization+code+or+state")

    stored = _state_store.pop(state, None)
    if not stored:
        # Redirect to frontend with an error
        return RedirectResponse(url="/?error=Invalid+or+expired+state")

    oauth_service = OAuthService(db)
    try:
        result = await oauth_service.handle_callback(code, state, state)
        log.info("OAuth callback success", user_id=result["user_id"])
        return RedirectResponse(url=f"/?user_id={result['user_id']}")
    except AppException as e:
        return RedirectResponse(url=f"/?error={e.detail}")


@router.get("/me", response_model=MeResponse)
async def me(
    user_id: int = Query(...),
    db: AsyncSession = Depends(get_session),
):
    oauth_service = OAuthService(db)
    token = await oauth_service.token_repo.get_by_user_id(user_id)
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")

    user = await oauth_service.user_repo.get_by_id(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    return MeResponse(
        id=user.id,
        linkedin_member_id=user.linkedin_member_id,
        full_name=user.full_name,
        email=user.email,
        profile_picture=user.profile_picture,
    )
