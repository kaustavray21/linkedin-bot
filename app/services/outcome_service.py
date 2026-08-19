"""
app/services/outcome_service.py

How one of your posts did, measured against your own posts.

**Against yourself, never against the exemplar.** A creator with 100k followers
gets 400 reactions on a mediocre post; you might get 12 on an excellent one.
That ratio measures follower count, not writing, and follower counts are not
reliably available to normalise it away. The two numbers do different jobs:

  * the exemplar's stats answer "was this worth cloning?" — used at selection
    time, in ranking.py
  * your own stats answer "did this choice work for me?" — used afterwards,
    against your own rolling median, which is what lives here

Collapsing them into one ratio produces a confident number that means nothing.

## Two things this refuses to do

**Compare across ages.** A post at 24 hours and one at seven days are answering
different questions, so the median is built from each other post's reading
*nearest the same age*, and readings outside the tolerance are left out rather
than stretched to fit.

**Report a median nobody could believe.** Below `outcome_min_samples` other
posts the comparison is `not_enough_data`, with the sample size attached. A
recommendation backed by three posts has to say "3 posts".

## Why engagement is reactions alone

Reactions are the count that reads reliably from a public page. Comments are
frequently absent — LinkedIn omits the anchor entirely at zero — so folding them
in would mean either treating unknown as zero, which is the trap the ranking
exists to avoid, or producing a composite that means something different from
one post to the next. Comments are carried alongside for display instead.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.database.models import Post, PostMetric

# What the comparison rests on, said out loud rather than implied by a null.
MEASURED = "measured"
NOT_ENOUGH_DATA = "not_enough_data"
NOT_MEASURED = "not_measured"


@dataclass
class PostOutcome:
    post_id: int
    age_hours: int | None = None
    engagement: int | None = None
    comments: int | None = None
    median: float | None = None
    ratio: float | None = None
    sample_size: int = 0
    basis: str = NOT_MEASURED

    @property
    def is_comparable(self) -> bool:
        return self.basis == MEASURED


def _engagement(metric: PostMetric | None) -> int | None:
    return metric.reactions if metric else None


async def _readings(db: AsyncSession, post_id: int) -> list[PostMetric]:
    return list(
        (
            await db.execute(
                select(PostMetric)
                .where(PostMetric.post_id == post_id)
                .order_by(PostMetric.captured_at)
            )
        ).scalars().all()
    )


def _nearest_by_age(readings: list[PostMetric], target_age: int) -> PostMetric | None:
    """The reading closest to `target_age`, or None if none is close enough.

    Returning the nearest reading regardless of distance would silently compare
    a one-day-old post against week-old ones, so anything outside the tolerance
    is dropped instead.
    """
    usable = [m for m in readings if m.age_hours is not None and m.reactions is not None]
    if not usable:
        return None

    best = min(usable, key=lambda m: abs(m.age_hours - target_age))
    if abs(best.age_hours - target_age) > settings.outcome_age_tolerance_hours:
        return None
    return best


async def rolling_median(
    db: AsyncSession, user_id: int, target_age_hours: int, exclude_post_id: int
) -> tuple[float | None, int]:
    """The median engagement of your *other* posts at a comparable age.

    Returns (median, sample_size). The sample size is returned even when the
    median is None, because "we looked and found two" is different information
    from "we did not look".
    """
    others = (
        await db.execute(
            select(Post).where(
                Post.user_id == user_id,
                Post.status == "published",
                Post.id != exclude_post_id,
            )
        )
    ).scalars().all()

    values: list[int] = []
    for other in others:
        near = _nearest_by_age(await _readings(db, other.id), target_age_hours)
        if near is not None:
            values.append(near.reactions)

    if len(values) < settings.outcome_min_samples:
        return None, len(values)
    return statistics.median(values), len(values)


async def outcome_for(db: AsyncSession, post: Post) -> PostOutcome:
    """How this post is doing against your own baseline at the same age."""
    readings = await _readings(db, post.id)
    latest = next(
        (m for m in reversed(readings) if m.reactions is not None), None
    )

    engagement = _engagement(latest)
    if latest is None or engagement is None or latest.age_hours is None:
        # Never measured, or measured and unreadable. Either way there is
        # nothing to compare, and saying so beats reporting a zero.
        return PostOutcome(post_id=post.id, basis=NOT_MEASURED)

    median, sample = await rolling_median(db, post.user_id, latest.age_hours, post.id)

    if median is None:
        return PostOutcome(
            post_id=post.id,
            age_hours=latest.age_hours,
            engagement=engagement,
            comments=latest.comments,
            sample_size=sample,
            basis=NOT_ENOUGH_DATA,
        )

    return PostOutcome(
        post_id=post.id,
        age_hours=latest.age_hours,
        engagement=engagement,
        comments=latest.comments,
        median=median,
        # A median of zero would make every ratio infinite. It is a real
        # outcome — nothing you posted got a reaction at that age — so the
        # engagement still shows, but the ratio does not pretend to exist.
        ratio=(engagement / median) if median > 0 else None,
        sample_size=sample,
        basis=MEASURED,
    )


async def outcomes_for_user(db: AsyncSession, user_id: int) -> list[PostOutcome]:
    """Every published post's outcome, newest first."""
    posts = (
        await db.execute(
            select(Post)
            .where(Post.user_id == user_id, Post.status == "published")
            # NOT .nullslast(): that emits PostgreSQL's NULLS LAST, which MySQL
            # rejects outright with error 1064. `IS NULL` yields 0 for a row that
            # has a date and 1 for one that does not, so ascending on it puts
            # dated rows first and undated last on every dialect.
            .order_by(Post.published_time.is_(None), Post.published_time.desc())
        )
    ).scalars().all()

    return [await outcome_for(db, post) for post in posts]
