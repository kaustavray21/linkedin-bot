from __future__ import annotations

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import OAuthException
from app.core.logger import get_logger
from app.core.security import generate_state
from app.repositories.token_repository import TokenRepository
from app.repositories.user_repository import UserRepository

log = get_logger(tag="oauth")


class OAuthService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.user_repo = UserRepository(session)
        self.token_repo = TokenRepository(session)

    def get_authorization_url(self) -> tuple[str, str]:
        state = generate_state()
        params = {
            "response_type": "code",
            "client_id": settings.client_id,
            "redirect_uri": settings.redirect_uri,
            "state": state,
            "scope": "w_member_social openid profile email",
        }
        from urllib.parse import urlencode
        auth_url = f"{settings.linkedin_auth_url}?{urlencode(params)}"
        return auth_url, state

    async def handle_callback(self, code: str, state: str, stored_state: str) -> dict:
        from app.core.security import validate_state

        if not validate_state(state, stored_state):
            raise OAuthException("Invalid state parameter. Possible CSRF attack.")

        token_data = await self._exchange_code(code)
        access_token = token_data.get("access_token", "")
        refresh_token = token_data.get("refresh_token")
        expires_in = token_data.get("expires_in")

        user_info = await self._fetch_user_info(access_token)
        linkedin_id = user_info.get("sub", "")
        if not linkedin_id:
            raise OAuthException("Failed to retrieve LinkedIn member ID")

        from datetime import datetime, timezone, timedelta
        expires_at = (datetime.now(timezone.utc) + timedelta(seconds=expires_in)).replace(tzinfo=None) if expires_in else None

        user = await self.user_repo.get_by_linkedin_id(linkedin_id)
        if user:
            await self.user_repo.update(
                user,
                full_name=user_info.get("name"),
                email=user_info.get("email"),
                profile_picture=user_info.get("picture"),
            )
        else:
            user = await self.user_repo.create(
                linkedin_member_id=linkedin_id,
                full_name=user_info.get("name"),
                email=user_info.get("email"),
                profile_picture=user_info.get("picture"),
            )

        existing_token = await self.token_repo.get_by_user_id(user.id)
        if existing_token:
            await self.token_repo.update(
                existing_token,
                access_token=access_token,
                refresh_token=refresh_token,
                expires_at=expires_at,
            )
        else:
            await self.token_repo.create(
                user_id=user.id,
                access_token=access_token,
                refresh_token=refresh_token,
                expires_at=expires_at,
            )

        log.info("OAuth callback handled", user_id=user.id)
        return {"user_id": user.id, "linkedin_id": linkedin_id}

    async def _exchange_code(self, code: str) -> dict:
        async with httpx.AsyncClient(timeout=30) as client:
            data = {
                "grant_type": "authorization_code",
                "code": code,
                "client_id": settings.client_id,
                "client_secret": settings.client_secret,
                "redirect_uri": settings.redirect_uri,
            }
            headers = {"Content-Type": "application/x-www-form-urlencoded"}
            response = await client.post(settings.linkedin_token_url, data=data, headers=headers)
            if response.is_error:
                log.error("Token exchange failed", status_code=response.status_code)
                raise OAuthException(f"Token exchange failed: {response.text}")
            return response.json()

    async def _fetch_user_info(self, access_token: str) -> dict:
        from app.core.constants import LINKEDIN_USERINFO_URL

        async with httpx.AsyncClient(timeout=30) as client:
            headers = {"Authorization": f"Bearer {access_token}"}
            response = await client.get(LINKEDIN_USERINFO_URL, headers=headers)
            if response.is_error:
                log.error("Failed to fetch user info", status_code=response.status_code)
                raise OAuthException(f"Failed to fetch user info: {response.text}")
            return response.json()

    async def refresh_access_token(self, user_id: int) -> str:
        token = await self.token_repo.get_by_user_id(user_id)
        if not token or not token.refresh_token:
            raise OAuthException("No refresh token available")

        async with httpx.AsyncClient(timeout=30) as client:
            data = {
                "grant_type": "refresh_token",
                "refresh_token": token.refresh_token,
                "client_id": settings.client_id,
                "client_secret": settings.client_secret,
            }
            headers = {"Content-Type": "application/x-www-form-urlencoded"}
            response = await client.post(settings.linkedin_token_url, data=data, headers=headers)
            if response.is_error:
                log.error("Token refresh failed", status_code=response.status_code)
                raise OAuthException(f"Token refresh failed: {response.text}")

            token_data = response.json()
            new_access_token = token_data.get("access_token", "")
            new_refresh_token = token_data.get("refresh_token")
            expires_in = token_data.get("expires_in")

            from datetime import datetime, timezone, timedelta
            expires_at = (datetime.now(timezone.utc) + timedelta(seconds=expires_in)).replace(tzinfo=None) if expires_in else None

            await self.token_repo.update(
                token,
                access_token=new_access_token,
                refresh_token=new_refresh_token or token.refresh_token,
                expires_at=expires_at,
            )

            return new_access_token
