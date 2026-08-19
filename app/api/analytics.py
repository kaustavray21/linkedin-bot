"""
app/api/analytics.py

How your published posts are doing, measured against your own baseline.

The observation stage: this reports and changes nothing. No ranking is biased
and no default is altered by what appears here — that comes later, and only
once a combination has enough samples to mean something. Everything exposed
here states what it rests on, including when the answer is "not enough data".
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.connection import get_session
from app.database.models import Post, PostMetric
from app.services.metrics_service import capture_due, permalink_for
from app.services.outcome_service import outcome_for

router = APIRouter(prefix="/analytics", tags=["analytics"])


class OutcomeResponse(BaseModel):
    post_id: int
    preview: str
    published_time: datetime | None
    permalink: str | None

    age_hours: int | None
    engagement: int | None
    comments: int | None
    median: float | None
    ratio: float | None
    sample_size: int
    # measured | not_enough_data | not_measured — always stated, never implied
    # by a null, so the interface can say why a number is missing.
    basis: str


class MetricPointResponse(BaseModel):
    captured_at: datetime | None
    age_hours: int | None
    reactions: int | None
    comments: int | None
    impressions: int | None
    source: str


class CaptureResponse(BaseModel):
    considered: int
    captured: int
    failed: int
    stopped: str | None
    notes: list[str]


def _preview(post: Post) -> str:
    first = next((line.strip() for line in (post.content or "").split("\n") if line.strip()), "")
    return first[:80] if first else "(no text)"


@router.get("/outcomes", response_model=list[OutcomeResponse])
async def list_outcomes(user_id: int, db: AsyncSession = Depends(get_session)):
    """Every published post with its comparison, newest first."""
    posts = (
        await db.execute(
            select(Post)
            .where(Post.user_id == user_id, Post.status == "published")
            .order_by(Post.published_time.desc().nullslast())
        )
    ).scalars().all()

    out: list[OutcomeResponse] = []
    for post in posts:
        outcome = await outcome_for(db, post)
        out.append(OutcomeResponse(
            post_id=post.id,
            preview=_preview(post),
            published_time=post.published_time,
            permalink=permalink_for(post),
            age_hours=outcome.age_hours,
            engagement=outcome.engagement,
            comments=outcome.comments,
            median=outcome.median,
            ratio=outcome.ratio,
            sample_size=outcome.sample_size,
            basis=outcome.basis,
        ))
    return out


@router.get("/posts/{post_id}/series", response_model=list[MetricPointResponse])
async def post_series(post_id: int, db: AsyncSession = Depends(get_session)):
    """Every reading for one post, oldest first.

    The series rather than the latest number, because a post that died in six
    hours and one still climbing on day seven look identical at a single point.
    """
    rows = (
        await db.execute(
            select(PostMetric)
            .where(PostMetric.post_id == post_id)
            .order_by(PostMetric.captured_at)
        )
    ).scalars().all()

    return [
        MetricPointResponse(
            captured_at=r.captured_at,
            age_hours=r.age_hours,
            reactions=r.reactions,
            comments=r.comments,
            impressions=r.impressions,
            source=r.source,
        )
        for r in rows
    ]


@router.post("/capture", response_model=CaptureResponse)
async def capture_now(db: AsyncSession = Depends(get_session)):
    """Take readings now, for anything due.

    Runs the same bounded path the scheduler does — the daily ceiling, the
    capture window and the circuit check all still apply, so pressing this
    repeatedly cannot turn into a request storm.
    """
    summary = await capture_due(db)
    await db.commit()
    return CaptureResponse(
        considered=summary.considered,
        captured=summary.captured,
        failed=summary.failed,
        stopped=summary.stopped,
        notes=summary.notes,
    )
