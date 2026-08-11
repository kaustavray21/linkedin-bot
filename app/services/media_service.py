"""
app/services/media_service.py

Ingests user-supplied images — uploaded from disk or fetched from a URL — and
normalises them into the same local file shape the AI image path already
produces (/static/uploads/<uuid>.png).

Keeping the output identical to AIService._save_raw_image() matters: the
publish path (linkedin_service.upload_image) resolves an image by taking the
filename and joining it to settings.uploads_dir. Any other path shape would
publish fine in testing and fail at upload time.

Two hazards are handled here rather than at the API layer, so they cannot be
bypassed by a future caller:

  1. Declared content types lie. A file called avatar.png containing a JPEG
     would be uploaded to LinkedIn labelled PNG (linkedin_service picks the mime
     from the extension), and rejected. Every image is sniffed and re-encoded.

  2. Fetching a user-supplied URL turns this service into an HTTP client the
     user controls — the classic SSRF shape. Cloud metadata endpoints and
     private ranges are blocked on every redirect hop, not just the first.
"""

from __future__ import annotations

import ipaddress
import socket
import uuid
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import httpx
from PIL import Image, UnidentifiedImageError

from app.core.config import settings
from app.core.logger import get_logger

log = get_logger(tag="media")

# Sniffed from content, never from the filename or the Content-Type header.
MAGIC_SIGNATURES: tuple[tuple[bytes, str], ...] = (
    (b"\x89PNG\r\n\x1a\n", "png"),
    (b"\xff\xd8\xff", "jpeg"),
    (b"GIF87a", "gif"),
    (b"GIF89a", "gif"),
)

ALLOWED_FORMATS = {"png", "jpeg", "webp", "gif"}
MAX_REDIRECTS = 3


class MediaError(ValueError):
    """Raised for anything the caller should see as a 400."""


@dataclass
class StoredImage:
    image_url: str
    image_source: str
    width: int
    height: int
    bytes_written: int


def sniff_format(data: bytes) -> str | None:
    """Identify an image by its leading bytes.

    WEBP needs a two-part check — "RIFF" then "WEBP" at offset 8 — because RIFF
    is also used by AVI and WAV.
    """
    for signature, name in MAGIC_SIGNATURES:
        if data.startswith(signature):
            return name
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None


def _is_blocked_ip(ip: str) -> bool:
    """True for any address that must never be fetched on a user's behalf."""
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return True

    return (
        addr.is_private          # 10/8, 172.16/12, 192.168/16, fc00::/7
        or addr.is_loopback      # 127/8, ::1
        or addr.is_link_local    # 169.254/16 — cloud metadata lives here
        or addr.is_reserved
        or addr.is_multicast
        or addr.is_unspecified
    )


def assert_url_is_fetchable(url: str) -> None:
    """Reject URLs that resolve anywhere we must not reach.

    Resolution happens here, before the request, and again for every redirect
    target — a public hostname that 302s to 169.254.169.254 is the whole point
    of the attack.
    """
    parsed = urlparse(url)

    if parsed.scheme not in ("http", "https"):
        raise MediaError(f"Only http and https URLs are supported (got '{parsed.scheme or 'none'}')")
    if not parsed.hostname:
        raise MediaError("URL has no host")

    try:
        resolved = socket.getaddrinfo(parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80))
    except socket.gaierror as exc:
        raise MediaError(f"Could not resolve host '{parsed.hostname}'") from exc

    for family, _type, _proto, _canon, sockaddr in resolved:
        ip = sockaddr[0]
        if _is_blocked_ip(ip):
            log.warning("Blocked SSRF-shaped fetch", url=url, resolved_ip=ip)
            raise MediaError(
                f"Refusing to fetch '{parsed.hostname}' — it resolves to a private "
                f"or reserved address ({ip})"
            )


def normalise_and_store(data: bytes, source: str) -> StoredImage:
    """Re-encode arbitrary image bytes into a clean PNG under uploads_dir.

    Re-encoding is not cosmetic. It strips EXIF (which carries GPS coordinates
    from phone photos), it defuses polyglot files that are valid in two formats
    at once, and it guarantees the on-disk extension matches the real content —
    which the LinkedIn upload path depends on.
    """
    if not data:
        raise MediaError("Image is empty")

    if len(data) > settings.max_upload_bytes:
        raise MediaError(
            f"Image is {len(data) // 1024}KB — the limit is {settings.max_upload_bytes // 1024}KB"
        )

    detected = sniff_format(data)
    if detected is None:
        raise MediaError("File does not look like a PNG, JPEG, WEBP or GIF image")
    if detected not in ALLOWED_FORMATS:
        raise MediaError(f"Unsupported image format: {detected}")

    from io import BytesIO

    try:
        with Image.open(BytesIO(data)) as img:
            img.load()
            # Flatten transparency onto white — LinkedIn renders posts on a
            # light card, so an unflattened alpha channel reads as a black box.
            if img.mode in ("RGBA", "LA", "P"):
                img = img.convert("RGBA")
                backdrop = Image.new("RGB", img.size, (255, 255, 255))
                backdrop.paste(img, mask=img.split()[-1])
                img = backdrop
            else:
                img = img.convert("RGB")

            width, height = img.size
            uploads = Path(settings.uploads_dir)
            uploads.mkdir(parents=True, exist_ok=True)
            filename = f"{uuid.uuid4().hex}.png"
            path = uploads / filename
            img.save(path, format="PNG", optimize=True)
    except UnidentifiedImageError as exc:
        raise MediaError("Could not decode the image") from exc
    except OSError as exc:
        raise MediaError(f"Could not process the image: {exc}") from exc

    written = path.stat().st_size
    log.info("Image stored", path=str(path), source=source, bytes=written)

    # Path shape must match AIService._save_raw_image — the publish path
    # depends on it.
    return StoredImage(
        image_url=f"/static/uploads/{filename}",
        image_source=source,
        width=width,
        height=height,
        bytes_written=written,
    )


async def fetch_image_from_url(url: str) -> StoredImage:
    """Download a remote image, validating every hop, then store it locally."""
    current = url

    async with httpx.AsyncClient(
        timeout=settings.media_fetch_timeout,
        follow_redirects=False,          # each hop is validated by hand
    ) as client:
        for _hop in range(MAX_REDIRECTS + 1):
            assert_url_is_fetchable(current)

            response = await client.get(current, headers={"User-Agent": "linkedin-bot/1.0"})

            if response.is_redirect:
                location = response.headers.get("location")
                if not location:
                    raise MediaError("Redirect without a destination")
                current = str(httpx.URL(current).join(location))
                continue

            if response.is_error:
                raise MediaError(f"Fetch failed with HTTP {response.status_code}")

            content_type = response.headers.get("content-type", "")
            if content_type and not content_type.startswith("image/"):
                raise MediaError(f"URL returned '{content_type or 'unknown'}', not an image")

            data = response.content
            if len(data) > settings.max_upload_bytes:
                raise MediaError(
                    f"Image is {len(data) // 1024}KB — the limit is "
                    f"{settings.max_upload_bytes // 1024}KB"
                )
            return normalise_and_store(data, source="url")

    raise MediaError(f"Too many redirects (limit {MAX_REDIRECTS})")
