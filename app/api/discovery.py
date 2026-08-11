from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.database.connection import get_session
from app.database.models import DiscoveredPost, DiscoveryJob
from app.services.discovery.egress.strategies import available_strategies
from app.services.discovery.fetcher import fetcher
from app.services.discovery.providers import (
    available_providers,
    get_provider,
    normalise_post_url,
)
from app.services.discovery.service import (
    purge_expired,
    purge_post,
    run_discovery,
    start_job,
)

router = APIRouter(prefix="/discovery", tags=["discovery"])


class SearchRequest(BaseModel):
    keyword: str
    limit: int = 10
    provider: str | None = None


class JobResponse(BaseModel):
    id: int
    keyword: str
    provider: str
    status: str
    requested_count: int
    found_count: int
    fetched_count: int
    parse_failures: int
    error: str | None
    created_at: datetime | None
    completed_at: datetime | None


class DiscoveredPostResponse(BaseModel):
    id: int
    keyword: str
    source: str
    post_url: str
    author_name: str | None
    author_headline: str | None
    content_text: str | None
    snippet: str | None
    hashtags: list[str]
    image_url: str | None
    posted_at: datetime | None
    reactions: int | None
    comments: int | None
    reposts: int | None
    metrics_source: str
    engagement_score: float
    used_as_reference: bool
    reviewed_at: datetime | None
    purged_at: datetime | None
    has_content: bool


class StatusResponse(BaseModel):
    provider: str
    available_providers: list[str]
    egress: str
    egress_fallback: str
    available_egress: list[str]
    daily_cap: int
    remaining_today: int
    circuits: dict


def _job_to_response(job: DiscoveryJob) -> JobResponse:
    return JobResponse(
        id=job.id,
        keyword=job.keyword,
        provider=job.provider,
        status=job.status,
        requested_count=job.requested_count,
        found_count=job.found_count,
        fetched_count=job.fetched_count,
        parse_failures=job.parse_failures,
        error=job.error,
        created_at=job.created_at,
        completed_at=job.completed_at,
    )


def _post_to_response(post: DiscoveredPost) -> DiscoveredPostResponse:
    return DiscoveredPostResponse(
        id=post.id,
        keyword=post.keyword,
        source=post.source,
        post_url=post.post_url,
        author_name=post.author_name,
        author_headline=post.author_headline,
        content_text=post.content_text,
        snippet=post.snippet,
        hashtags=post.hashtags or [],
        image_url=post.image_url,
        posted_at=post.posted_at,
        reactions=post.reactions,
        comments=post.comments,
        reposts=post.reposts,
        metrics_source=post.metrics_source,
        engagement_score=post.engagement_score,
        used_as_reference=post.used_as_reference,
        reviewed_at=post.reviewed_at,
        purged_at=post.purged_at,
        has_content=bool(post.content_text and post.content_text.strip()),
    )


@router.get("/status", response_model=StatusResponse)
async def discovery_status() -> StatusResponse:
    """What the pipeline is configured to do and how much budget is left."""
    state = fetcher.status()
    return StatusResponse(
        provider=settings.discovery_provider,
        available_providers=available_providers(),
        egress=settings.discovery_egress,
        egress_fallback=settings.discovery_egress_fallback,
        available_egress=available_strategies(),
        daily_cap=state["daily_cap"],
        remaining_today=state["remaining_today"],
        circuits=state["circuits"],
    )


@router.post("/search", response_model=JobResponse, status_code=202)
async def search(
    body: SearchRequest,
    user_id: int | None = Query(None),
    db: AsyncSession = Depends(get_session),
) -> JobResponse:
    """Start discovering posts for a topic. Returns immediately.

    The work runs in the background because the fetcher deliberately paces at
    ~30s per post — a ten-post run takes over five minutes. Doing that inside
    the request meant the client blocked for the whole run AND saw nothing when
    it finished polling, because the single request transaction had not
    committed yet. Poll GET /discovery/jobs/{id} for progress; posts become
    queryable one at a time as they are stored.
    """
    keyword = body.keyword.strip()
    if not keyword:
        raise HTTPException(status_code=400, detail="A keyword is required")

    provider = get_provider(body.provider)
    job = DiscoveryJob(
        user_id=user_id,
        keyword=keyword,
        provider=provider.name,
        status="queued",
        requested_count=max(1, min(25, body.limit)),
    )
    db.add(job)
    # Commit before handing the id to a background task with its own session —
    # an uncommitted row is invisible to it.
    await db.commit()
    await db.refresh(job)

    start_job(job.id)
    return _job_to_response(job)


@router.post("/manual", response_model=JobResponse)
async def add_manual(
    body: SearchRequest,
    user_id: int | None = Query(None),
    db: AsyncSession = Depends(get_session),
) -> JobResponse:
    """Add one specific post by URL — the escape hatch when search misses it."""
    if not normalise_post_url(body.keyword):
        raise HTTPException(status_code=400, detail="That does not look like a LinkedIn post URL")

    job = await run_discovery(
        db=db, keyword=body.keyword.strip(), limit=1,
        provider_name="manual", user_id=user_id,
    )
    return _job_to_response(job)


@router.get("/jobs/{job_id}", response_model=JobResponse)
async def get_job(job_id: int, db: AsyncSession = Depends(get_session)) -> JobResponse:
    job = (
        await db.execute(select(DiscoveryJob).where(DiscoveryJob.id == job_id))
    ).scalar_one_or_none()
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return _job_to_response(job)


@router.get("/posts", response_model=list[DiscoveredPostResponse])
async def list_posts(
    keyword: str | None = Query(None),
    sort: str = Query("engagement", pattern="^(engagement|recent)$"),
    limit: int = Query(50, ge=1, le=200),
    include_purged: bool = Query(False),
    db: AsyncSession = Depends(get_session),
) -> list[DiscoveredPostResponse]:
    stmt = select(DiscoveredPost)
    if keyword:
        stmt = stmt.where(DiscoveredPost.keyword == keyword)
    if not include_purged:
        stmt = stmt.where(DiscoveredPost.purged_at.is_(None))

    stmt = stmt.order_by(
        DiscoveredPost.engagement_score.desc()
        if sort == "engagement"
        else DiscoveredPost.fetched_at.desc()
    ).limit(limit)

    posts = (await db.execute(stmt)).scalars().all()
    return [_post_to_response(p) for p in posts]


@router.post("/posts/{post_id}/reviewed", response_model=DiscoveredPostResponse)
async def mark_reviewed(
    post_id: int, db: AsyncSession = Depends(get_session)
) -> DiscoveredPostResponse:
    post = await _get_post(db, post_id)
    from datetime import timezone as _tz

    post.reviewed_at = datetime.now(_tz.utc).replace(tzinfo=None)
    await db.flush()
    return _post_to_response(post)


@router.post("/posts/{post_id}/use-as-reference", response_model=DiscoveredPostResponse)
async def use_as_reference(
    post_id: int, db: AsyncSession = Depends(get_session)
) -> DiscoveredPostResponse:
    post = await _get_post(db, post_id)
    if not (post.content_text and post.content_text.strip()):
        raise HTTPException(
            status_code=400,
            detail="This post has no readable text, so its structure cannot be cloned",
        )
    post.used_as_reference = True
    await db.flush()
    return _post_to_response(post)


@router.delete("/posts/{post_id}", status_code=204)
async def delete_post(post_id: int, db: AsyncSession = Depends(get_session)) -> None:
    """Purge a post's content after review.

    Purges rather than deletes: the layout skeleton is kept so any draft already
    generated from this post stays reproducible, while the third-party wording
    is removed.
    """
    post = await _get_post(db, post_id)
    await purge_post(db, post)
    await db.flush()


@router.post("/purge")
async def purge(
    keyword: str | None = Query(None),
    reviewed_only: bool = Query(False),
    expired_only: bool = Query(False),
    db: AsyncSession = Depends(get_session),
) -> dict:
    if expired_only:
        return {"purged": await purge_expired(db)}

    stmt = select(DiscoveredPost).where(DiscoveredPost.purged_at.is_(None))
    if keyword:
        stmt = stmt.where(DiscoveredPost.keyword == keyword)
    if reviewed_only:
        stmt = stmt.where(DiscoveredPost.reviewed_at.is_not(None))

    posts = (await db.execute(stmt)).scalars().all()
    for post in posts:
        await purge_post(db, post)
    await db.flush()
    return {"purged": len(posts)}


async def _get_post(db: AsyncSession, post_id: int) -> DiscoveredPost:
    post = (
        await db.execute(select(DiscoveredPost).where(DiscoveredPost.id == post_id))
    ).scalar_one_or_none()
    if post is None:
        raise HTTPException(status_code=404, detail="Discovered post not found")
    return post
