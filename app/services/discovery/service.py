"""
app/services/discovery/service.py

Runs a discovery job end to end: search, fetch, parse, rank, persist.

Ordering matters here. Candidates are deduped against the database *before* any
fetching, so a post already seen is never requested from LinkedIn again. With a
daily fetch cap in force, spending budget re-reading known posts would mean
fewer new ones — the cache is a capacity decision, not just a speed one.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logger import get_logger
from app.database.models import DiscoveredPost, DiscoveryJob
from app.services.discovery.egress.base import EgressError
from app.services.discovery.fetcher import fetcher
from app.services.discovery.parser import parse_post
from app.services.discovery.providers import DiscoveredCandidate, get_provider
from app.services.discovery.ranking import compute_score, describe_basis
from app.services.layout_service import extract_skeleton

log = get_logger(tag="discovery")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


async def _existing_urls(db: AsyncSession, urls: list[str]) -> set[str]:
    if not urls:
        return set()
    result = await db.execute(
        select(DiscoveredPost.post_url).where(DiscoveredPost.post_url.in_(urls))
    )
    return set(result.scalars().all())


async def _bump_overlap(db: AsyncSession, url: str, keyword: str) -> None:
    """A post surfacing under a second query is weak evidence it is relevant."""
    existing = (
        await db.execute(select(DiscoveredPost).where(DiscoveredPost.post_url == url))
    ).scalar_one_or_none()
    if existing is None:
        return
    existing.query_overlap = (existing.query_overlap or 1) + 1
    existing.engagement_score = compute_score(
        reactions=existing.reactions,
        comments=existing.comments,
        reposts=existing.reposts,
        serp_rank=existing.serp_rank,
        posted_at=existing.posted_at,
        query_overlap=existing.query_overlap,
    )


async def run_discovery(
    db: AsyncSession,
    keyword: str,
    limit: int = 10,
    provider_name: str | None = None,
    user_id: int | None = None,
    commit_each: bool = False,
    stop_after_usable: int | None = None,
    job: DiscoveryJob | None = None,
) -> DiscoveryJob:
    """Discover posts for `keyword` and persist whatever could be read.

    commit_each: commit after every stored post instead of once at the end.
        Required whenever anything else needs to see results while the run is
        still going. The fetcher paces at ~30s per post, so a ten-post run takes
        five minutes — holding one transaction open for its whole duration makes
        every row invisible until the very last one lands.

    stop_after_usable: stop once this many posts with readable text are stored.
        Callers that need one exemplar should pass 1 rather than paying the full
        pacing cost for candidates they will not look at.

    job: an existing row to update, for runs started before the work begins.
    """
    provider = get_provider(provider_name)

    if job is None:
        job = DiscoveryJob(
            user_id=user_id,
            keyword=keyword,
            provider=provider.name,
            status="running",
            requested_count=limit,
        )
        db.add(job)
        await db.flush()
    else:
        job.status = "running"
        job.provider = provider.name

    async def _save() -> None:
        if commit_each:
            await db.commit()
        else:
            await db.flush()

    await _save()

    outcome = await provider.search(keyword, limit)
    job.found_count = len(outcome.candidates)

    if outcome.error and not outcome.candidates:
        job.status = "failed"
        job.error = outcome.error
        job.completed_at = _utcnow()
        await _save()
        return job

    known = await _existing_urls(db, [c.post_url for c in outcome.candidates])
    fresh = [c for c in outcome.candidates if c.post_url not in known]

    for candidate in outcome.candidates:
        if candidate.post_url in known:
            await _bump_overlap(db, candidate.post_url, keyword)

    log.info(
        "Discovery search complete",
        keyword=keyword,
        provider=provider.name,
        found=len(outcome.candidates),
        already_known=len(known),
        to_fetch=len(fresh),
    )

    stored = 0
    usable = 0
    parse_failures = 0
    stop_reason: str | None = None

    for candidate in fresh:
        try:
            result = await fetcher.fetch(candidate.post_url)
        except EgressError as exc:
            # Budget exhausted or circuit open. Both mean stop the whole run —
            # continuing would just burn through the remaining candidates
            # producing identical failures.
            stop_reason = str(exc)
            log.warning("Halting discovery run", reason=stop_reason)
            break

        parsed = parse_post(result)
        if parsed.has_content:
            usable += 1
        else:
            parse_failures += 1

        post = _build_post(candidate, parsed, keyword, user_id)
        db.add(post)
        stored += 1

        # Update counts alongside the post itself, so a caller polling the job
        # sees progress that matches what is actually queryable.
        job.fetched_count = stored
        job.parse_failures = parse_failures
        await _save()

        if stop_after_usable is not None and usable >= stop_after_usable:
            log.info("Stopping early — enough usable posts", usable=usable)
            break

    job.fetched_count = stored
    job.parse_failures = parse_failures
    job.completed_at = _utcnow()

    if stop_reason:
        job.status = "partial"
        job.error = stop_reason
    elif stored and parse_failures == stored:
        # Everything fetched, nothing readable — that is a parser or markup
        # problem, and saying "success" would hide it.
        job.status = "partial"
        job.error = "Fetched pages but could not extract post content from any of them"
    else:
        job.status = "success"

    await _save()
    return job


# Strong references to running jobs. asyncio only holds a weak reference to a
# task, so without this a discovery run can be garbage collected mid-flight.
_running_jobs: set = set()


async def execute_job(job_id: int) -> None:
    """Run a discovery job to completion on its own session.

    Separate from the request session on purpose: the run takes minutes, and
    tying it to a request means the client waits for all of it and sees nothing
    until it ends. Owning the session here also means this code controls when it
    commits, which is what makes partial results visible.
    """
    from app.database.connection import get_session_factory

    session_factory = get_session_factory()
    async with session_factory() as session:
        try:
            job = (
                await session.execute(select(DiscoveryJob).where(DiscoveryJob.id == job_id))
            ).scalar_one_or_none()
            if job is None:
                log.error("Discovery job vanished before it could run", job_id=job_id)
                return

            await run_discovery(
                db=session,
                keyword=job.keyword,
                limit=job.requested_count,
                provider_name=job.provider,
                user_id=job.user_id,
                commit_each=True,
                job=job,
            )
        except Exception as exc:
            log.exception("Discovery job failed", job_id=job_id)
            await session.rollback()
            # Record the failure on the job row, or it stays "running" forever
            # and the UI polls a job that will never finish.
            try:
                job = (
                    await session.execute(select(DiscoveryJob).where(DiscoveryJob.id == job_id))
                ).scalar_one_or_none()
                if job is not None:
                    job.status = "failed"
                    job.error = str(exc)
                    job.completed_at = _utcnow()
                    await session.commit()
            except Exception:
                log.exception("Could not record the job failure", job_id=job_id)


def start_job(job_id: int) -> None:
    """Fire a discovery job into the background and keep a reference to it."""
    import asyncio

    task = asyncio.create_task(execute_job(job_id))
    _running_jobs.add(task)
    task.add_done_callback(_running_jobs.discard)


def _build_post(
    candidate: DiscoveredCandidate,
    parsed,
    keyword: str,
    user_id: int | None,
) -> DiscoveredPost:
    text = parsed.content_text or candidate.snippet or None

    skeleton = None
    if text and text.strip():
        try:
            skeleton = extract_skeleton(text).to_dict()
        except ValueError:
            skeleton = None

    return DiscoveredPost(
        user_id=user_id,
        keyword=keyword,
        source=candidate.source or "unknown",
        post_url=candidate.post_url,
        author_name=parsed.author_name or candidate.author_name,
        author_headline=parsed.author_headline,
        author_profile_url=parsed.author_profile_url,
        content_text=parsed.content_text,
        snippet=candidate.snippet or None,
        hashtags=parsed.hashtags or [],
        image_url=parsed.image_url,
        posted_at=parsed.posted_at,
        reactions=parsed.reactions,
        comments=parsed.comments,
        reposts=parsed.reposts,
        metrics_source=describe_basis(parsed.reactions, parsed.comments, parsed.reposts),
        serp_rank=candidate.serp_rank,
        query_overlap=1,
        engagement_score=compute_score(
            reactions=parsed.reactions,
            comments=parsed.comments,
            reposts=parsed.reposts,
            serp_rank=candidate.serp_rank,
            posted_at=parsed.posted_at,
            query_overlap=1,
        ),
        layout_skeleton=skeleton,
        fetched_at=_utcnow(),
        expires_at=_utcnow() + timedelta(days=settings.discovery_retention_days),
    )


async def purge_post(db: AsyncSession, post: DiscoveredPost) -> None:
    """Drop the third-party content, keep the structure.

    The skeleton holds no wording from the source — only block and line counts —
    so retaining it carries none of the copyright or terms-of-service weight
    that the raw text does, while keeping drafts already built from it
    reproducible.
    """
    post.content_text = None
    post.snippet = None
    post.raw_payload = None
    post.image_url = None
    post.purged_at = _utcnow()


async def purge_expired(db: AsyncSession) -> int:
    """Purge everything past its retention date. Safe to run repeatedly."""
    result = await db.execute(
        select(DiscoveredPost).where(
            DiscoveredPost.expires_at.is_not(None),
            DiscoveredPost.expires_at <= _utcnow(),
            DiscoveredPost.purged_at.is_(None),
        )
    )
    posts = list(result.scalars().all())
    for post in posts:
        await purge_post(db, post)
    if posts:
        log.info("Purged expired discovered posts", count=len(posts))
    return len(posts)
