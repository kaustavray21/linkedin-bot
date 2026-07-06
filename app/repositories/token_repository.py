from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import OAuthToken


class TokenRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, user_id: int, access_token: str, refresh_token: str | None = None, expires_at=None, scope: str | None = None) -> OAuthToken:
        token = OAuthToken(
            user_id=user_id,
            access_token=access_token,
            refresh_token=refresh_token,
            expires_at=expires_at,
            scope=scope,
        )
        self.session.add(token)
        await self.session.flush()
        await self.session.refresh(token)
        return token

    async def get_by_user_id(self, user_id: int) -> OAuthToken | None:
        result = await self.session.execute(
            select(OAuthToken).where(OAuthToken.user_id == user_id).order_by(OAuthToken.created_at.desc())
        )
        return result.scalar_one_or_none()

    async def update(self, token: OAuthToken, **kwargs) -> OAuthToken:
        for key, value in kwargs.items():
            setattr(token, key, value)
        await self.session.flush()
        await self.session.refresh(token)
        return token

    async def delete(self, token: OAuthToken) -> None:
        await self.session.delete(token)
        await self.session.flush()
