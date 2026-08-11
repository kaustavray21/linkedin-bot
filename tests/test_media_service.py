from __future__ import annotations

import io
from unittest.mock import patch

import pytest
from PIL import Image

from app.core.config import settings
from app.services.media_service import (
    MediaError,
    assert_url_is_fetchable,
    normalise_and_store,
    sniff_format,
)


@pytest.fixture(autouse=True)
def isolated_uploads(tmp_path, monkeypatch):
    """Keep test images out of the real uploads directory — otherwise every run
    leaves orphaned PNGs in the folder the app serves."""
    monkeypatch.setattr(settings, "uploads_dir", str(tmp_path / "uploads"))


def _png_bytes(size=(40, 30), mode="RGB") -> bytes:
    buf = io.BytesIO()
    Image.new(mode, size, (120, 80, 200)).save(buf, format="PNG")
    return buf.getvalue()


def _jpeg_bytes(size=(40, 30)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, (10, 200, 90)).save(buf, format="JPEG")
    return buf.getvalue()


# ---------------------------------------------------------------- sniffing --

def test_sniff_identifies_real_formats():
    assert sniff_format(_png_bytes()) == "png"
    assert sniff_format(_jpeg_bytes()) == "jpeg"
    assert sniff_format(b"GIF89a" + b"\x00" * 20) == "gif"


def test_sniff_rejects_non_images():
    assert sniff_format(b"#!/bin/sh\nrm -rf /") is None
    assert sniff_format(b"") is None


def test_riff_container_that_is_not_webp_is_rejected():
    """RIFF also fronts AVI and WAV — matching on RIFF alone would let them
    through as images."""
    fake_avi = b"RIFF" + b"\x00\x00\x00\x00" + b"AVI " + b"\x00" * 20
    assert sniff_format(fake_avi) is None


def test_jpeg_named_png_is_caught_by_content_not_extension():
    """linkedin_service picks the upload mime from the file extension, so a
    mislabelled file would be sent to LinkedIn with the wrong content type."""
    stored = normalise_and_store(_jpeg_bytes(), source="upload")
    assert stored.image_url.endswith(".png")      # re-encoded, so now truly PNG
    assert stored.image_source == "upload"


def test_oversize_upload_rejected():
    with pytest.raises(MediaError, match="limit"):
        normalise_and_store(b"\x89PNG\r\n\x1a\n" + b"\x00" * (settings.max_upload_bytes + 10), source="upload")


def test_empty_upload_rejected():
    with pytest.raises(MediaError, match="empty"):
        normalise_and_store(b"", source="upload")


def test_valid_signature_but_corrupt_body_rejected():
    """A file can start with the PNG magic bytes and still be undecodable."""
    with pytest.raises(MediaError):
        normalise_and_store(b"\x89PNG\r\n\x1a\n" + b"garbage" * 10, source="upload")


def test_stored_path_matches_the_ai_image_convention():
    """publish_post resolves images by filename against uploads_dir; a different
    path shape would pass tests and fail at LinkedIn upload time."""
    stored = normalise_and_store(_png_bytes(), source="upload")
    assert stored.image_url.startswith("/static/uploads/")
    assert stored.image_url.endswith(".png")


def test_transparency_is_flattened_not_dropped():
    stored = normalise_and_store(_png_bytes(mode="RGBA"), source="upload")
    assert stored.width == 40 and stored.height == 30


# -------------------------------------------------------------------- SSRF --

@pytest.mark.parametrize(
    "host,ip",
    [
        ("metadata.example", "169.254.169.254"),   # cloud metadata
        ("localhost.example", "127.0.0.1"),        # loopback
        ("internal.example", "10.1.2.3"),          # private class A
        ("intranet.example", "192.168.1.10"),      # private class C
        ("corp.example", "172.16.5.5"),            # private class B
        ("zero.example", "0.0.0.0"),               # unspecified
    ],
)
def test_private_and_reserved_addresses_are_refused(host, ip):
    with patch(
        "app.services.media_service.socket.getaddrinfo",
        return_value=[(2, 1, 6, "", (ip, 443))],
    ):
        with pytest.raises(MediaError, match="private or reserved"):
            assert_url_is_fetchable(f"https://{host}/logo.png")


def test_public_address_is_allowed():
    with patch(
        "app.services.media_service.socket.getaddrinfo",
        return_value=[(2, 1, 6, "", ("93.184.216.34", 443))],
    ):
        assert_url_is_fetchable("https://example.com/logo.png")


def test_non_http_schemes_refused():
    for url in ("file:///etc/passwd", "gopher://x/1", "ftp://host/f.png"):
        with pytest.raises(MediaError, match="http and https"):
            assert_url_is_fetchable(url)


def test_ipv6_loopback_refused():
    with patch(
        "app.services.media_service.socket.getaddrinfo",
        return_value=[(10, 1, 6, "", ("::1", 443, 0, 0))],
    ):
        with pytest.raises(MediaError, match="private or reserved"):
            assert_url_is_fetchable("https://v6.example/x.png")


def test_host_with_mixed_public_and_private_records_is_refused():
    """DNS returning one public and one private A record must not be treated as
    safe on the strength of the public one."""
    with patch(
        "app.services.media_service.socket.getaddrinfo",
        return_value=[
            (2, 1, 6, "", ("93.184.216.34", 443)),
            (2, 1, 6, "", ("169.254.169.254", 443)),
        ],
    ):
        with pytest.raises(MediaError, match="private or reserved"):
            assert_url_is_fetchable("https://rebind.example/x.png")


def test_unresolvable_host_refused():
    import socket as _socket

    with patch(
        "app.services.media_service.socket.getaddrinfo",
        side_effect=_socket.gaierror("nope"),
    ):
        with pytest.raises(MediaError, match="Could not resolve"):
            assert_url_is_fetchable("https://nx.example/x.png")


@pytest.mark.asyncio
async def test_redirect_to_private_address_is_refused():
    """The attack this exists for: a public host that 302s to the metadata IP.
    Validating only the first URL would sail straight into it."""
    import httpx

    from app.services import media_service

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "public.example":
            return httpx.Response(302, headers={"location": "http://169.254.169.254/latest/meta-data/"})
        return httpx.Response(200, content=_png_bytes(), headers={"content-type": "image/png"})

    def fake_getaddrinfo(host, port, *a, **kw):
        ip = "93.184.216.34" if host == "public.example" else "169.254.169.254"
        return [(2, 1, 6, "", (ip, port or 443))]

    transport = httpx.MockTransport(handler)
    original = httpx.AsyncClient

    class PatchedClient(original):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    with patch.object(media_service.socket, "getaddrinfo", fake_getaddrinfo), \
         patch.object(media_service.httpx, "AsyncClient", PatchedClient):
        with pytest.raises(MediaError, match="private or reserved"):
            await media_service.fetch_image_from_url("https://public.example/logo.png")


@pytest.mark.asyncio
async def test_non_image_content_type_refused():
    import httpx

    from app.services import media_service

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"<html>hi</html>", headers={"content-type": "text/html"})

    transport = httpx.MockTransport(handler)
    original = httpx.AsyncClient

    class PatchedClient(original):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    with patch.object(
        media_service.socket, "getaddrinfo",
        return_value=[(2, 1, 6, "", ("93.184.216.34", 443))],
    ), patch.object(media_service.httpx, "AsyncClient", PatchedClient):
        with pytest.raises(MediaError, match="not an image"):
            await media_service.fetch_image_from_url("https://example.com/page.html")


@pytest.mark.asyncio
async def test_successful_fetch_stores_image():
    import httpx

    from app.services import media_service

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=_png_bytes(), headers={"content-type": "image/png"})

    transport = httpx.MockTransport(handler)
    original = httpx.AsyncClient

    class PatchedClient(original):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    with patch.object(
        media_service.socket, "getaddrinfo",
        return_value=[(2, 1, 6, "", ("93.184.216.34", 443))],
    ), patch.object(media_service.httpx, "AsyncClient", PatchedClient):
        stored = await media_service.fetch_image_from_url("https://example.com/logo.png")

    assert stored.image_source == "url"
    assert stored.image_url.startswith("/static/uploads/")
