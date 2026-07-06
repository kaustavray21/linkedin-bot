from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.services.ai_service import AIService

router = APIRouter(prefix="/generate", tags=["generate"])


class TextGenerateRequest(BaseModel):
    prompt: str
    provider: str = "gemini"


class TextGenerateResponse(BaseModel):
    content: str


class ImageGenerateRequest(BaseModel):
    prompt: str


class ImageGenerateResponse(BaseModel):
    image_url: str


@router.post("/text", response_model=TextGenerateResponse)
async def generate_text(body: TextGenerateRequest):
    """Generate LinkedIn post text from a prompt using AI."""
    service = AIService(provider=body.provider)
    try:
        content = await service.generate_content(body.prompt)
        return TextGenerateResponse(content=content)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/image", response_model=ImageGenerateResponse)
async def generate_image(body: ImageGenerateRequest):
    """Generate an image from a prompt using Gemini Imagen and return its URL."""
    service = AIService()
    try:
        image_url = await service.generate_image(body.prompt)
        return ImageGenerateResponse(image_url=image_url)
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))
