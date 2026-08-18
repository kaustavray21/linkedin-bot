"""
app/services/discovery/service.py

Runs a discovery job end to end: search, fetch, parse, rank, persist.

Ordering matters here. Candidates are deduped against the database *before* any
fetching, so a post already seen is never requested from LinkedIn again. With a
daily fetch cap in force, spending budget re-reading known posts would mean
fewer new ones — the cache is a capacity decision, not just a speed one.

Fetching runs in parallel; persistence does not. AsyncSession is documented as
"not safe for use in concurrent tasks", so the fetch workers never touch `db` —
they return their results and this module writes them one at a time. Dispatch
happens in waves so an early stop still bounds how much budget is spent.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logger import get_logger
from app.database.models import DiscoveredPost, DiscoveryJob, DraftLineage
from app.services.discovery.egress.base import EgressError
from app.services.discovery.fetcher import fetcher
from app.services.discovery.parser import parse_post
from app.services.discovery.providers import DiscoveredCandidate, get_provider
from app.services.discovery.ranking import compute_score, describe_basis
from app.services.layout_service import extract_skeleton
from app.services.post_type_service import load_taxonomy, propose_type, resolve_proposal

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
    hashtags: list[str] | None = None,
    timelimit: str | None = None,
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

    outcome = await provider.search(
        keyword, limit, hashtags=hashtags, timelimit=timelimit
    )
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
    collisions = 0
    classified_new = 0
    classify_refusals = 0
    stop_reason: str | None = None

    # Read once per run, as plain dicts. Anything a coined type adds mid-run is
    # still caught: the near-duplicate guard in resolve_proposal compares against
    # the live table, not against this snapshot.
    taxonomy = await load_taxonomy(db) if settings.discovery_classify else []

    async def _fetch_one(candidate: DiscoveredCandidate):
        """Runs concurrently. Touches the network and nothing else.

        Specifically, it never touches `db`. AsyncSession is documented as "not
        safe for use in concurrent tasks", and interleaving fetches with writes
        — as this function's caller used to — raises "Session is already
        flushing" the moment the loop goes parallel. Fetch in parallel, write
        serially: this is the parallel half.
        """
        try:
            return candidate, await fetcher.fetch(candidate.post_url), None
        except EgressError as exc:
            return candidate, None, exc

    async def _classify_wave(parsed_posts: list, current: list[dict]) -> list:
        """Classify a wave concurrently. Touches the network, never `db`.

        Returns one entry per post, aligned by position, so the serial section
        can zip the two together. None means "do not classify this one" — either
        the feature is off or there was no text to read.
        """
        if not settings.discovery_classify:
            return [None] * len(parsed_posts)

        async def _one(parsed):
            text = (parsed.content_text or "").strip()
            if not text:
                return None
            try:
                return await propose_type(text, current)
            except Exception as exc:
                # Classification is an enrichment. A failure here must never
                # cost the post, which is the expensive thing on this path.
                log.warning("Classification failed for a post", error=str(exc))
                return None

        return await asyncio.gather(*(_one(p) for p in parsed_posts))

    # Dispatched in waves rather than all at once so `stop_after_usable` still
    # means something. A single gather over every candidate spends the whole
    # budget before the first result lands; a wave bounds the overshoot to the
    # worker count.
    wave_size = max(1, settings.discovery_concurrency_max)

    for offset in range(0, len(fresh), wave_size):
        if fetcher.any_circuit_open():
            stop_reason = "All egress strategies are cooling down after repeated blocks."
            log.warning("Halting discovery run before dispatch", reason=stop_reason)
            break

        wave = fresh[offset : offset + wave_size]
        results = await asyncio.gather(*(_fetch_one(c) for c in wave))

        # Parsing is pure and cheap, so it happens here rather than in the
        # workers — but it has to happen before classification, which needs the
        # post text.
        read: list[tuple[DiscoveredCandidate, object]] = []
        for candidate, result, error in results:
            if error is not None:
                # Budget exhausted or circuit open. Both mean stop the whole run
                # — continuing would just burn through the remaining candidates
                # producing identical failures.
                stop_reason = str(error)
                continue

            parsed = parse_post(result)
            if parsed.has_content:
                usable += 1
            else:
                parse_failures += 1
            read.append((candidate, parsed))

        # Second parallel stage. Classification is a model call per post, so
        # running the wave serially would add minutes to a pipeline tuned to
        # seconds. Like _fetch_one it touches the network and never `db`; the
        # taxonomy was copied to plain dicts for exactly this crossing.
        proposals = await _classify_wave([p for _, p in read], taxonomy)

        # Serial section — one task, one session.
        for (candidate, parsed), proposal in zip(read, proposals):
            post = _build_post(candidate, parsed, keyword, user_id)

            if proposal is not None:
                # Resolution writes, so it belongs here rather than in the
                # workers. A refusal leaves post_type_slug NULL — an
                # unclassified post is fine; a wrongly classified one pollutes
                # the taxonomy every later classification is prompted with.
                resolution = await resolve_proposal(db, proposal)
                post.post_type_slug = resolution.slug
                if resolution.created:
                    classified_new += 1
                elif resolution.refused:
                    classify_refusals += 1

            try:
                # Savepoint per row: two jobs searching at once can both reach
                # the same URL after the dedup check above, and `post_url` is
                # unique. Without this, one duplicate aborts the whole run.
                async with db.begin_nested():
                    db.add(post)
            except IntegrityError:
                collisions += 1
                continue

            stored += 1

        # Counts are written alongside the posts themselves, so a caller polling
        # the job sees progress that matches what is actually queryable.
        job.fetched_count = stored
        job.parse_failures = parse_failures
        await _save()

        if stop_reason:
            log.warning("Halting discovery run", reason=stop_reason)
            break

        if stop_after_usable is not None and usable >= stop_after_usable:
            log.info(
                "Stopping early — enough usable posts",
                usable=usable,
                fetched=offset + len(wave),
            )
            break

    if collisions:
        log.info("Absorbed duplicate posts already stored", count=collisions)

    if settings.discovery_classify:
        log.info(
            "Classification complete",
            new_types=classified_new,
            refused=classify_refusals,
        )

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
                hashtags=job.hashtags or None,
                timelimit=job.timelimit,
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


async def record_lineage(
    db: AsyncSession,
    post_id: int | None,
    exemplar: DiscoveredPost,
    params: dict | None = None,
) -> DraftLineage:
    """Snapshot the exemplar onto the draft, at generation time.

    A copy, not a join. The discovered post is hard-deleted at 30 days, and a
    lineage row holding only its id would go blank exactly when the record
    starts being interesting. The skeleton comes along for the same reason it
    used to survive a purge: no wording from the source, and it is what keeps an
    already-generated draft reproducible.
    """
    lineage = DraftLineage(
        post_id=post_id,
        discovered_post_id=exemplar.id,
        exemplar_url=exemplar.post_url,
        exemplar_author=exemplar.author_name,
        exemplar_snippet=(exemplar.content_text or exemplar.snippet or "")[:400] or None,
        exemplar_reactions=exemplar.reactions,
        exemplar_comments=exemplar.comments,
        exemplar_captured_at=exemplar.fetched_at,
        exemplar_skeleton=exemplar.layout_skeleton,
        params_used=params or {},
        used_at=_utcnow(),
    )
    db.add(lineage)
    exemplar.used_as_reference = True
    await db.flush()
    return lineage


async def delete_expired(db: AsyncSession) -> int:
    """Remove discovered posts past their retention date.

    A real delete, not a purge. Third-party content is not ours to keep, and 30
    days was the answer. Drafts built from these posts stay reproducible because
    draft_lineage already holds the snapshot, and its foreign key is ON DELETE
    SET NULL so the history survives the row going away.
    """
    result = await db.execute(
        select(DiscoveredPost).where(
            DiscoveredPost.expires_at.is_not(None),
            DiscoveredPost.expires_at <= _utcnow(),
        )
    )
    posts = list(result.scalars().all())
    if not posts:
        return 0

    # Detach the lineage explicitly rather than trusting ON DELETE SET NULL.
    # That constraint is enforced by the database, which means two things go
    # wrong if we rely on it: SQLAlchemy's session keeps whatever value it
    # already loaded, so in-flight objects still point at a deleted row, and
    # SQLite does not enforce foreign keys unless PRAGMA foreign_keys is on —
    # so it may not fire at all. The constraint stays as a backstop for deletes
    # that happen outside this function.
    ids = [p.id for p in posts]
    await db.execute(
        update(DraftLineage)
        .where(DraftLineage.discovered_post_id.in_(ids))
        .values(discovered_post_id=None)
        .execution_options(synchronize_session="fetch")
    )

    for post in posts:
        await db.delete(post)
    log.info("Deleted expired discovered posts", count=len(posts))
    return len(posts)


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
