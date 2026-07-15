from __future__ import annotations

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.connection import get_session
from app.services.ai_service import AIService

router = APIRouter(prefix="/generate", tags=["generate"])


class TextGenerateRequest(BaseModel):
    prompt: str
    provider: str = "gemini"
    num_words: int | None = None
    num_paragraphs: int | None = None


class TextGenerateResponse(BaseModel):
    content: str


class TextVariationsResponse(BaseModel):
    variations: list[str]


class ImageGenerateRequest(BaseModel):
    prompt: str


class ImageGenerateResponse(BaseModel):
    image_url: str


@router.post("/text", response_model=TextGenerateResponse)
async def generate_text(body: TextGenerateRequest):
    """Generate LinkedIn post text from a prompt using AI."""
    service = AIService(body.provider)
    try:
        content = await service.generate_content(
            body.prompt,
            num_words=body.num_words,
            num_paragraphs=body.num_paragraphs
        )
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


class StyledPostGenerateRequest(BaseModel):
    topic: str
    user_notes: str | None = ""
    profile_slug: str = "combined"
    selected_posts: list[str] | None = None
    num_words: int | None = None
    num_paragraphs: int | None = None
    num_variations: int = 1
    hook_style: str | None = None
    line_rhythm: str | None = None
    word_type: str | None = None


class StyledImageGenerateRequest(BaseModel):
    post_text: str


@router.post("/styled-post", response_model=TextVariationsResponse)
async def generate_styled_post_endpoint(
    body: StyledPostGenerateRequest,
    db: AsyncSession = Depends(get_session)
):
    """Generate style-conditioned LinkedIn post text with variations."""
    try:
        from app.services.content_generation_service import generate_styled_post
        import asyncio
        
        # Cap variations between 1 and 3 to limit api load
        num_vars = max(1, min(3, body.num_variations))
        
        tasks = []
        for i in range(num_vars):
            tasks.append(
                generate_styled_post(
                    topic=body.topic,
                    user_notes=body.user_notes or "",
                    db=db,
                    profile_slug=body.profile_slug,
                    selected_posts=body.selected_posts,
                    num_words=body.num_words,
                    num_paragraphs=body.num_paragraphs,
                    variation_index=i,
                    hook_style=body.hook_style,
                    line_rhythm=body.line_rhythm,
                    word_type=body.word_type
                )
            )
        variations = await asyncio.gather(*tasks)
        return TextVariationsResponse(variations=list(variations))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/styled-image", response_model=ImageGenerateResponse)
async def generate_styled_image_endpoint(body: StyledImageGenerateRequest):
    """Generate image based on derived prompt from post text."""
    try:
        from app.services.image_prompt_service import derive_image_prompt
        prompt = await derive_image_prompt(body.post_text)
        service = AIService()
        image_url = await service.generate_image(prompt)
        return ImageGenerateResponse(image_url=image_url)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

