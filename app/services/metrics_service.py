"""
app/services/metrics_service.py

Reads how your own published posts are performing, over time.

The route is deliberately the public one. A published post is a public LinkedIn
post with a permalink, so the fetcher and parser Discovery already uses can read
its reaction and comment counts — no `r_member_postAnalytics` scope, no
re-consent, no API quota. Verified against real published posts before this was
built: both returned HTTP 200 with counts parsed and no authwall.

Impressions and reposts are the exception. Those genuinely need the member
analytics API, so they stay NULL until that scope is granted. A NULL here means
"not measured" and never zero — the same rule the ranking rests on.

## The flood guard

"Refresh every 24h or on restart" is one crash-loop away from a request storm.
Three bounds, all in config:

  * a post is due only if its newest reading is older than the refresh interval
  * nothing is read after the capture window, by which point engagement has
    settled and re-reading spends requests to learn nothing
  * a per-day ceiling, mirroring the discovery budget

Captures also stop entirely while discovery's circuit breaker is open. Being
blocked is a property of this IP, not of one subsystem, and continuing to fetch
through a block is what escalates a soft throttle into a hard one.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logger import get_logger
from app.database.models import Post, PostMetric
from app.services.discovery.egress.base import EgressError
from app.services.discovery.egress.strategies import DirectEgress
from app.services.discovery.fetcher import fetcher
from app.services.discovery.parser import parse_post

log = get_logger(tag="metrics")

# The three URN kinds LinkedIn issues for a share. A bare numeric id is not
# enough: the kinds take different paths, so guessing one produces a plausible
# dead link rather than an obvious failure. Mirrors linkedInPermalink() in app.js.
URN_RE = re.compile(r"^urn:li:(share|ugcPost|activity):\d+$")


@dataclass
class CaptureSummary:
    considered: int = 0
    captured: int = 0
    failed: int = 0
    stopped: str | None = None
    notes: list[str] = field(default_factory=list)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def permalink_for(post: Post) -> str | None:
    """The public URL of a published post, or None when it has no usable URN."""
    urn = (post.linkedin_post_id or "").strip()
    if not urn or not URN_RE.match(urn):
        return None
    return f"https://www.linkedin.com/feed/update/{quote(urn)}/"


async def _captures_today(db: AsyncSession) -> int:
    midnight = _utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    return await db.scalar(
        select(func.count()).select_from(PostMetric).where(PostMetric.captured_at >= midnight)
    ) or 0


async def _latest_capture(db: AsyncSession, post_id: int) -> datetime | None:
    return await db.scalar(
        select(func.max(PostMetric.captured_at)).where(PostMetric.post_id == post_id)
    )


async def due_posts(db: AsyncSession) -> list[Post]:
    """Published posts whose newest reading has gone stale.

    Ordered oldest-reading-first so a run that hits the daily ceiling starves
    the posts already measured most recently rather than whichever the database
    happened to return first.
    """
    window_start = _utcnow() - timedelta(days=settings.metrics_capture_window_days)

    candidates = (
        await db.execute(
            select(Post).where(
                Post.status == "published",
                Post.linkedin_post_id.isnot(None),
                Post.published_time.isnot(None),
                Post.published_time >= window_start,
            )
        )
    ).scalars().all()

    cutoff = _utcnow() - timedelta(hours=settings.metrics_refresh_interval_hours)
    due: list[tuple[datetime, Post]] = []
    for post in candidates:
        if permalink_for(post) is None:
            continue
        latest = await _latest_capture(db, post.id)
        if latest is None:
            # Never measured. Sorted first so a brand-new post gets its opening
            # reading before anything already has a series.
            due.append((datetime.min, post))
        elif latest < cutoff:
            due.append((latest, post))

    due.sort(key=lambda pair: pair[0])
    return [post for _, post in due]


async def capture_post(db: AsyncSession, post: Post) -> PostMetric | None:
    """Read one post's public page and store a reading. Returns None on failure.

    A failure is logged and dropped rather than raised: one unreadable post must
    not stop the rest of the run, and a gap in a time series is honest where a
    fabricated point would not be.
    """
    url = permalink_for(post)
    if url is None:
        return None

    try:
        result = await DirectEgress().fetch(url)
    except EgressError as exc:
        log.warning("Metrics fetch failed", post_id=post.id, error=str(exc))
        return None

    if not result.ok:
        log.warning("Metrics fetch returned no usable page",
                    post_id=post.id, status=result.status_code)
        return None

    parsed = parse_post(result)
    if parsed.hit_authwall:
        # An authwalled page reads as zero engagement rather than as an error,
        # which would quietly flatten the series.
        log.warning("Metrics fetch hit an authwall", post_id=post.id)
        return None

    age_hours = None
    if post.published_time:
        age_hours = max(int((_utcnow() - post.published_time).total_seconds() // 3600), 0)

    metric = PostMetric(
        post_id=post.id,
        captured_at=_utcnow(),
        age_hours=age_hours,
        reactions=parsed.reactions,
        comments=parsed.comments,
        source="public_page",
    )
    db.add(metric)
    return metric


async def capture_due(db: AsyncSession, limit: int | None = None) -> CaptureSummary:
    """Take a reading of every post that is due, within the day's budget."""
    summary = CaptureSummary()

    if not settings.metrics_enabled:
        summary.stopped = "metrics capture is disabled"
        return summary

    if fetcher.any_circuit_open():
        # Being blocked belongs to this IP, not to one subsystem. Reading on
        # through it is what turns a soft throttle into a hard block.
        summary.stopped = "all egress strategies are cooling down after repeated blocks"
        log.warning("Skipping metrics capture", reason=summary.stopped)
        return summary

    spent = await _captures_today(db)
    budget = max(settings.metrics_daily_capture_cap - spent, 0)
    if budget == 0:
        summary.stopped = f"daily capture cap reached ({settings.metrics_daily_capture_cap})"
        return summary

    posts = await due_posts(db)
    summary.considered = len(posts)

    allowed = min(budget, limit) if limit is not None else budget
    if len(posts) > allowed:
        # Said out loud rather than silently truncated: a run that measured half
        # the posts must not read as a run that measured all of them.
        summary.notes.append(
            f"{len(posts)} due, {allowed} captured this run — the rest are next time"
        )
        posts = posts[:allowed]

    for post in posts:
        metric = await capture_post(db, post)
        if metric is None:
            summary.failed += 1
        else:
            summary.captured += 1

    await db.flush()
    log.info(
        "Metrics capture complete",
        considered=summary.considered,
        captured=summary.captured,
        failed=summary.failed,
    )
    return summary
