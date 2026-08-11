"""
app/services/remix_service.py

The full automatic path: a topic goes in, a reviewable draft comes out.

Composition, not new logic — discovery finds and ranks posts, layout_service
captures a shape, content_generation writes to that shape, similarity_service
polices the result, and the image path mirrors the same borrow-the-form,
not-the-substance rule that governs the text.

The one thing this module decides on its own is which discovered post becomes
the exemplar, and it prefers the highest-ranked post that actually has readable
text — a post whose body could not be extracted has no shape to clone, however
well it performed.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logger import get_logger
from app.database.models import DiscoveredPost
from app.services.ai_service import AIService
from app.services.content_generation_service import generate_with_layout
from app.services.discovery.service import run_discovery
from app.services.hashtag_service import extract_tags, remix_hashtags
from app.services.image_prompt_service import derive_image_prompt
from app.services.similarity_service import SimilarityReport
from app.services.style_service import extract_style_profile

log = get_logger()


@dataclass
class RemixResult:
    text: str
    hashtags: list[str] = field(default_factory=list)
    image_url: str | None = None
    image_style_note: str | None = None
    exemplar_id: int | None = None
    exemplar_url: str | None = None
    exemplar_author: str | None = None
    similarity: SimilarityReport | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def full_text(self) -> str:
        """Body plus the remixed hashtags, and only those.

        The model writes its own hashtag block when the exemplar has one, but
        those tags bypassed remix_hashtags() and so were never checked against
        the source's. Its block is dropped and the policed set appended instead.
        """
        from app.services.hashtag_service import strip_trailing_hashtag_block

        body = strip_trailing_hashtag_block(self.text)
        if not self.hashtags:
            return body
        return f"{body}\n\n{' '.join(self.hashtags)}"


async def pick_exemplar(
    db: AsyncSession, keyword: str | None = None
) -> DiscoveredPost | None:
    """Highest-ranked discovered post that still has usable text."""
    stmt = select(DiscoveredPost).where(
        DiscoveredPost.purged_at.is_(None),
        DiscoveredPost.content_text.is_not(None),
    )
    if keyword:
        stmt = stmt.where(DiscoveredPost.keyword == keyword)

    stmt = stmt.order_by(DiscoveredPost.engagement_score.desc()).limit(10)
    for post in (await db.execute(stmt)).scalars().all():
        if post.content_text and post.content_text.strip():
            return post
    return None


async def generate_style_matched_image(
    post_text: str,
    reference_image_url: str | None,
    ai_service: AIService | None = None,
) -> tuple[str | None, str | None]:
    """Create an original image, optionally echoing a reference's visual language.

    The reference image is described, never reused. Republishing someone's
    graphic is a copyright problem that no amount of prompt engineering fixes;
    borrowing its palette and composition is the visual equivalent of what the
    text pipeline already does with structure.
    """
    ai = ai_service or AIService(provider="gemini")
    style_note: str | None = None

    if reference_image_url:
        try:
            async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
                response = await client.get(reference_image_url)
            if not response.is_error and response.content:
                mime = response.headers.get("content-type", "image/png").split(";")[0]
                style_note = await ai.describe_image_style(response.content, mime=mime)
        except Exception:
            # A missing style cue is a downgrade, not a failure — we still make
            # an image, just without the visual echo.
            log.exception("Could not derive a style note from the reference image")

    prompt = await derive_image_prompt(post_text)
    if style_note:
        prompt = f"{prompt}. Visual style guidance: {style_note}"

    try:
        image_url = await ai.generate_image(prompt)
    except Exception:
        log.exception("Image generation failed")
        return None, style_note

    return image_url, style_note


async def remix_from_post(
    db: AsyncSession,
    topic: str,
    exemplar: DiscoveredPost,
    user_notes: str = "",
    with_image: bool = True,
    variation_index: int = 0,
) -> RemixResult:
    """Build a draft shaped like `exemplar`, about `topic`."""
    source_text = exemplar.content_text or ""
    if not source_text.strip():
        raise ValueError("That post has no readable text, so its structure cannot be cloned")

    style = extract_style_profile([source_text])

    text, report = await generate_with_layout(
        topic=topic,
        exemplar=source_text,
        user_notes=user_notes,
        style=style,
        variation_index=variation_index,
    )

    source_tags = exemplar.hashtags or extract_tags(source_text)
    hashtags = await remix_hashtags(source_tags, topic) if source_tags else []

    result = RemixResult(
        text=text,
        hashtags=hashtags,
        exemplar_id=exemplar.id,
        exemplar_url=exemplar.post_url,
        exemplar_author=exemplar.author_name,
        similarity=report,
    )

    if with_image:
        image_url, style_note = await generate_style_matched_image(
            post_text=text, reference_image_url=exemplar.image_url
        )
        result.image_url = image_url
        result.image_style_note = style_note
        if image_url is None:
            # Surfaced rather than raised: an imageless draft is still useful,
            # and publishing never required an image.
            result.notes.append("Image generation was unavailable — the text draft is unaffected.")

    return result


async def generate_from_topic(
    db: AsyncSession,
    topic: str,
    user_notes: str = "",
    limit: int = 8,
    with_image: bool = True,
    provider_name: str | None = None,
    user_id: int | None = None,
) -> RemixResult:
    """Topic in, draft out. Discovers first, then remixes the best result.

    Reuses posts already discovered for this topic when there are any — a repeat
    request should not spend fetch budget re-reading the same posts.
    """
    exemplar = await pick_exemplar(db, keyword=topic)
    notes: list[str] = []

    if exemplar is None:
        job = await run_discovery(
            db=db, keyword=topic, limit=limit,
            provider_name=provider_name, user_id=user_id,
            # One good exemplar is all this path needs. Fetching the whole
            # candidate list first would add ~30s of pacing per extra post to a
            # request the user is actively waiting on.
            stop_after_usable=1,
            commit_each=True,
        )
        exemplar = await pick_exemplar(db, keyword=topic)

        if exemplar is None:
            detail = job.error or "no posts with readable text were found"
            raise ValueError(
                f"Could not find a usable LinkedIn post for '{topic}' — {detail}"
            )
        notes.append(
            f"Discovered {job.fetched_count} post(s) via {job.provider}; "
            f"{job.parse_failures} could not be read."
        )
    else:
        notes.append("Used a previously discovered post for this topic.")

    result = await remix_from_post(
        db=db, topic=topic, exemplar=exemplar,
        user_notes=user_notes, with_image=with_image,
    )
    result.notes = notes + result.notes
    return result
