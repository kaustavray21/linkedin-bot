from __future__ import annotations

from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel

from app.core.config import settings
from app.services.media_service import (
    MediaError,
    fetch_image_from_url,
    normalise_and_store,
)

router = APIRouter(prefix="/media", tags=["media"])


class MediaResponse(BaseModel):
    image_url: str
    image_source: str
    width: int
    height: int
    bytes_written: int


class FromUrlRequest(BaseModel):
    url: str


@router.post("/upload", response_model=MediaResponse)
async def upload_image(file: UploadFile = File(...)) -> MediaResponse:
    """Store an image from the user's device."""
    # Read with a ceiling rather than trusting Content-Length, which is client
    # supplied and trivially wrong.
    data = await file.read(settings.max_upload_bytes + 1)
    try:
        stored = normalise_and_store(data, source="upload")
    except MediaError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return MediaResponse(**stored.__dict__)


@router.post("/from-url", response_model=MediaResponse)
async def image_from_url(body: FromUrlRequest) -> MediaResponse:
    """Store an image fetched from a public URL."""
    try:
        stored = await fetch_image_from_url(body.url)
    except MediaError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:  # network failures, malformed responses
        raise HTTPException(status_code=502, detail=f"Could not fetch image: {exc}")
    return MediaResponse(**stored.__dict__)
