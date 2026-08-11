from __future__ import annotations

import io
from unittest.mock import patch

import pytest
from httpx import AsyncClient
from PIL import Image

from app.core.config import settings


@pytest.fixture(autouse=True)
def isolated_uploads(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "uploads_dir", str(tmp_path / "uploads"))


def _png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (24, 24), (200, 30, 30)).save(buf, format="PNG")
    return buf.getvalue()


@pytest.mark.asyncio
async def test_upload_returns_a_publishable_path(async_client: AsyncClient):
    response = await async_client.post(
        "/media/upload", files={"file": ("photo.png", _png(), "image/png")}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["image_source"] == "upload"
    assert body["image_url"].startswith("/static/uploads/")
    assert body["width"] == 24


@pytest.mark.asyncio
async def test_upload_of_a_non_image_is_a_400_not_a_500(async_client: AsyncClient):
    response = await async_client.post(
        "/media/upload", files={"file": ("payload.png", b"#!/bin/sh\necho hi", "image/png")}
    )
    assert response.status_code == 400
    assert "does not look like" in response.json()["detail"]


@pytest.mark.asyncio
async def test_from_url_rejects_metadata_endpoint(async_client: AsyncClient):
    """End-to-end confirmation that the SSRF guard is actually wired into the
    route, not merely present in the service."""
    with patch(
        "app.services.media_service.socket.getaddrinfo",
        return_value=[(2, 1, 6, "", ("169.254.169.254", 80))],
    ):
        response = await async_client.post(
            "/media/from-url", json={"url": "http://metadata.internal/latest/meta-data/"}
        )
    assert response.status_code == 400
    assert "private or reserved" in response.json()["detail"]


@pytest.mark.asyncio
async def test_from_url_rejects_file_scheme(async_client: AsyncClient):
    response = await async_client.post("/media/from-url", json={"url": "file:///etc/passwd"})
    assert response.status_code == 400
    assert "http and https" in response.json()["detail"]
