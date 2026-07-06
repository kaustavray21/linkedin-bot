from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppException
from app.database.connection import get_session
from app.services.linkedin_service import LinkedInService
from app.services.token_service import TokenService

router = APIRouter(prefix="/profile", tags=["profile"])


@router.get("/")
async def get_profile(
    user_id: int = Query(...),
    db: AsyncSession = Depends(get_session),
):
    token_service = TokenService(db)
    try:
        access_token = await token_service.get_valid_access_token(user_id)
    except AppException as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail)

    linkedin_service = LinkedInService(access_token)
    try:
        profile = await linkedin_service.get_profile()
        return profile
    except AppException as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail)
