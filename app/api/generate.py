from __future__ import annotations

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, Field
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


class StyledImageGenerateRequest(BaseModel):
    post_text: str


class HashtagRequest(BaseModel):
    text: str | None = None
    exemplar_id: int | None = None
    topic: str | None = ""
    count: int | None = None


class HashtagResponse(BaseModel):
    hashtags: list[str]
    source: str          # "reference" | "post"


class RefineRequest(BaseModel):
    text: str
    instruction: str
    exemplar_id: int | None = None


class RefineResponse(BaseModel):
    text: str
    similarity_jaccard: float | None = None
    similarity_band: str | None = None
    similarity_checked: bool = False


@router.post("/hashtags", response_model=HashtagResponse)
async def generate_hashtags_endpoint(
    body: HashtagRequest,
    db: AsyncSession = Depends(get_session),
):
    """Two different jobs behind one route.

    With an exemplar we remix its tags — a rule about not copying, which only
    means something when there is a source. Without one we read the post and
    name what it is about. They are not the same function.
    """
    from sqlalchemy import select

    from app.database.models import DiscoveredPost
    from app.services.hashtag_service import derive_hashtags, extract_tags, remix_hashtags

    try:
        if body.exemplar_id is not None:
            exemplar = (
                await db.execute(
                    select(DiscoveredPost).where(DiscoveredPost.id == body.exemplar_id)
                )
            ).scalar_one_or_none()
            if exemplar is None:
                raise HTTPException(status_code=404, detail="Discovered post not found")

            source_tags = exemplar.hashtags or extract_tags(exemplar.content_text or "")
            if not source_tags:
                raise HTTPException(
                    status_code=422,
                    detail="That post has no hashtags to work from — try 'From my post'.",
                )
            tags = await remix_hashtags(
                source_tags, body.topic or "", count=body.count
            )
            return HashtagResponse(hashtags=tags, source="reference")

        tags = await derive_hashtags(body.text or "", count=body.count or 5)
        return HashtagResponse(hashtags=tags, source="post")
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


@router.post("/refine", response_model=RefineResponse)
async def refine_endpoint(
    body: RefineRequest,
    db: AsyncSession = Depends(get_session),
):
    """Rewrite a draft to one instruction, re-checking originality every time."""
    from sqlalchemy import select

    from app.database.models import DiscoveredPost
    from app.services.content_generation_service import refine_post

    exemplar_text: str | None = None
    if body.exemplar_id is not None:
        exemplar = (
            await db.execute(
                select(DiscoveredPost).where(DiscoveredPost.id == body.exemplar_id)
            )
        ).scalar_one_or_none()
        exemplar_text = exemplar.content_text if exemplar else None

    try:
        text, report = await refine_post(
            current_text=body.text,
            instruction=body.instruction,
            exemplar=exemplar_text,
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    return RefineResponse(
        text=text,
        similarity_jaccard=report.jaccard if report else None,
        similarity_band=report.band if report else None,
        # False means "no source post to compare against" — the UI must say that
        # rather than show a green badge that stands for nothing.
        similarity_checked=report is not None,
    )


class RemixRequest(BaseModel):
    topic: str
    exemplar_id: int
    user_notes: str | None = ""
    with_image: bool = True
    variation_index: int = 0
    # None keeps the exemplar's own paragraph count, which is the default a
    # clone should have. The bound matches the control in the UI.
    num_paragraphs: int | None = Field(default=None, ge=1, le=10)
    # None keeps however the exemplar itself was classified.
    post_type_slug: str | None = None
    # Notes from a Deep Think run. The client sends them so the research and the
    # generation stay separate calls — the user sees the findings before the
    # draft is written, rather than after.
    research: str | None = None


class FromTopicRequest(BaseModel):
    topic: str
    user_notes: str | None = ""
    limit: int = 8
    with_image: bool = True
    provider: str | None = None
    num_paragraphs: int | None = Field(default=None, ge=1, le=10)
    post_type_slug: str | None = None
    # Notes from a Deep Think run. The client sends them so the research and the
    # generation stay separate calls — the user sees the findings before the
    # draft is written, rather than after.
    research: str | None = None


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
            num_paragraphs=body.num_paragraphs,
            post_type_slug=body.post_type_slug,
            research=body.research,
        )
    except ValueError as e:
        # Expected outcomes — nothing discoverable, or nothing original enough.
        # These are user-actionable, not server faults.
        raise HTTPException(status_code=422, detail=str(e))
    return _remix_to_response(result)


class ResearchRequest(BaseModel):
    topic: str


class ResearchResponse(BaseModel):
    notes: str
    sources: list[dict]
    ok: bool
    reason: str | None
    pages_read: int


@router.post("/research", response_model=ResearchResponse)
async def research_endpoint(body: ResearchRequest):
    """Search and condense the web on a topic. Never fails the caller.

    A refusal comes back as ok=false with a reason rather than an error status,
    because "the sources did not cover this" is a result the interface has to
    show, not a fault to retry.
    """
    from app.services.research_service import research_topic

    result = await research_topic(body.topic)
    return ResearchResponse(
        notes=result.notes,
        sources=[{"title": s.title, "url": s.url} for s in result.sources],
        ok=result.ok,
        reason=result.reason,
        pages_read=result.pages_read,
    )


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
            num_paragraphs=body.num_paragraphs,
            post_type_slug=body.post_type_slug,
            research=body.research,
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

