from __future__ import annotations

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_login_returns_auth_url(async_client: AsyncClient) -> None:
    response = await async_client.get("/auth/login")
    assert response.status_code == 200
    data = response.json()
    assert "auth_url" in data
    assert data["auth_url"].startswith("https://www.linkedin.com/oauth/v2/authorization")


@pytest.mark.asyncio
async def test_callback_missing_params(async_client: AsyncClient) -> None:
    response = await async_client.get("/auth/callback")
    assert response.status_code == 422
