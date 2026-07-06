from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundException
from app.core.logger import get_logger
from app.repositories.token_repository import TokenRepository

log = get_logger(tag="oauth")


class TokenService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.token_repo = TokenRepository(session)

    async def get_valid_access_token(self, user_id: int) -> str:
        token = await self.token_repo.get_by_user_id(user_id)
        if not token:
            raise NotFoundException("No token found for user")

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        if token.expires_at and token.expires_at < now:
            from app.services.oauth_service import OAuthService
            oauth_service = OAuthService(self.session)
            return await oauth_service.refresh_access_token(user_id)

        return token.access_token

    async def revoke_token(self, user_id: int) -> None:
        token = await self.token_repo.get_by_user_id(user_id)
        if token:
            await self.token_repo.delete(token)
            log.info("Token revoked", user_id=user_id)
