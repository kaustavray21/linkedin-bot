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


class RemixRequest(BaseModel):
    topic: str
    exemplar_id: int
    user_notes: str | None = ""
    with_image: bool = True
    variation_index: int = 0


class FromTopicRequest(BaseModel):
    topic: str
    user_notes: str | None = ""
    limit: int = 8
    with_image: bool = True
    provider: str | None = None


class RemixResponse(BaseModel):
    text: str
    full_text: str
    hashtags: list[str]
    image_url: str | None
    image_style_note: str | None
    exemplar_id: int | None
    exemplar_url: str | None
    exemplar_author: str | None
    similarity_jaccard: float | None
    similarity_longest_run: int | None
    similarity_band: str | None
    notes: list[str]


def _remix_to_response(result) -> RemixResponse:
    return RemixResponse(
        text=result.text,
        full_text=result.full_text,
        hashtags=result.hashtags,
        image_url=result.image_url,
        image_style_note=result.image_style_note,
        exemplar_id=result.exemplar_id,
        exemplar_url=result.exemplar_url,
        exemplar_author=result.exemplar_author,
        similarity_jaccard=result.similarity.jaccard if result.similarity else None,
        similarity_longest_run=result.similarity.longest_run if result.similarity else None,
        similarity_band=result.similarity.band if result.similarity else None,
        notes=result.notes,
    )


@router.post("/from-topic", response_model=RemixResponse)
async def generate_from_topic_endpoint(
    body: FromTopicRequest,
    user_id: int | None = None,
    db: AsyncSession = Depends(get_session),
):
    """Full automatic path: discover posts for a topic, then draft one like them."""
    from app.services.remix_service import generate_from_topic

    topic = body.topic.strip()
    if not topic:
        raise HTTPException(status_code=400, detail="A topic is required")

    try:
        result = await generate_from_topic(
            db=db,
            topic=topic,
            user_notes=body.user_notes or "",
            limit=max(1, min(25, body.limit)),
            with_image=body.with_image,
            provider_name=body.provider,
            user_id=user_id,
        )
    except ValueError as e:
        # Expected outcomes — nothing discoverable, or nothing original enough.
        # These are user-actionable, not server faults.
        raise HTTPException(status_code=422, detail=str(e))
    return _remix_to_response(result)


@router.post("/remix", response_model=RemixResponse)
async def remix_endpoint(
    body: RemixRequest,
    db: AsyncSession = Depends(get_session),
):
    """Draft a post shaped like one specific discovered post."""
    from sqlalchemy import select

    from app.database.models import DiscoveredPost
    from app.services.remix_service import remix_from_post

    exemplar = (
        await db.execute(select(DiscoveredPost).where(DiscoveredPost.id == body.exemplar_id))
    ).scalar_one_or_none()
    if exemplar is None:
        raise HTTPException(status_code=404, detail="Discovered post not found")

    try:
        result = await remix_from_post(
            db=db,
            topic=body.topic.strip(),
            exemplar=exemplar,
            user_notes=body.user_notes or "",
            with_image=body.with_image,
            variation_index=body.variation_index,
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return _remix_to_response(result)


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

