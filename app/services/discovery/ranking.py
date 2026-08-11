"""
app/services/discovery/ranking.py

Orders discovered posts by how well they are probably performing.

Engagement counts are only sometimes present in public markup, so a ranking that
required them would silently discard most results. Instead the score blends
whatever signals are available and the UI states which basis it used.

The rule that keeps this honest: a missing count is None, never 0. Treating
"could not read the number" as "the number is zero" would push every unreadable
post to the bottom regardless of how it actually performed — a confident-looking
ordering built on absent data.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone

from app.core.config import settings


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def recency_factor(posted_at: datetime | None, half_life_days: float = 30.0) -> float:
    """1.0 for something posted now, decaying by half every `half_life_days`."""
    if posted_at is None:
        return 0.5          # unknown age: mid-range, neither rewarded nor punished
    age_days = max((_utcnow() - posted_at).total_seconds() / 86400.0, 0.0)
    return 0.5 ** (age_days / half_life_days)


def serp_factor(serp_rank: int | None) -> float:
    """Search rank as an authority proxy. Rank 1 scores 1.0, decaying after."""
    if not serp_rank or serp_rank < 1:
        return 0.0
    return 1.0 / math.sqrt(serp_rank)


def compute_score(
    reactions: int | None = None,
    comments: int | None = None,
    reposts: int | None = None,
    serp_rank: int | None = None,
    posted_at: datetime | None = None,
    query_overlap: int = 1,
) -> float:
    """Blend the available signals into a single orderable number.

    log1p compresses engagement so one viral post does not flatten the rest of
    the ranking into noise.
    """
    score = 0.0

    if reactions is not None:
        score += settings.rank_w_reactions * math.log1p(reactions)
    if comments is not None:
        score += settings.rank_w_comments * math.log1p(comments)
    if reposts is not None:
        score += settings.rank_w_reposts * math.log1p(reposts)

    score += settings.rank_w_serp * serp_factor(serp_rank)
    score += settings.rank_w_recency * recency_factor(posted_at)
    score += settings.rank_w_overlap * math.log1p(max(query_overlap - 1, 0))

    return round(score, 4)


def describe_basis(reactions: int | None, comments: int | None, reposts: int | None) -> str:
    """What the UI should tell the user this ranking rests on."""
    if any(v is not None for v in (reactions, comments, reposts)):
        return "measured"
    return "inferred"
