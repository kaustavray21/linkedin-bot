from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from app.core.config import settings
from app.database.models import Post, PostMetric, User
from app.services.discovery.egress.base import EgressError, FetchResult
from app.services.metrics_service import (
    capture_due,
    capture_post,
    due_posts,
    permalink_for,
)

# The markup shape S1 established: counts on the social-actions anchors.
PAGE = """
<html><head>
<script type="application/ld+json">
{"@type":"DiscussionForumPosting","articleBody":"My own post about shipping.",
 "datePublished":"2026-08-01T10:00:00Z","comment":[],"commentCount":0,
 "author":{"name":"Me"}}
</script></head><body><main><section class="mb-3">
<article class="relative container-lined main-feed-activity-card">
  <div class="flex items-center main-feed-activity-card__social-actions">
    <a data-test-id="social-actions__reactions" data-num-reactions="42">42</a>
    <a data-test-id="social-actions__comments" data-num-comments="7">7 Comments</a>
  </div>
</article>
</section></main></body></html>
"""


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


async def _user(db) -> User:
    user = User(linkedin_member_id="me", full_name="Me")
    db.add(user)
    await db.flush()
    return user


async def _post(db, user, *, urn="urn:li:share:123", published_days_ago=1,
                status="published") -> Post:
    post = Post(
        user_id=user.id, content="my post", status=status,
        linkedin_post_id=urn,
        published_time=_now() - timedelta(days=published_days_ago),
    )
    db.add(post)
    await db.flush()
    return post


@pytest.fixture(autouse=True)
def _metrics_on(monkeypatch):
    monkeypatch.setattr(settings, "metrics_enabled", True)
    monkeypatch.setattr("app.services.metrics_service.fetcher.any_circuit_open", lambda: False)


def _serve(page=PAGE, status=200):
    async def fake_fetch(self, url):
        return FetchResult(url, status, page, "html", egress="direct")
    return fake_fetch


# ------------------------------------------------------------------ permalink --

@pytest.mark.parametrize("urn, ok", [
    ("urn:li:share:7494223989318541312", True),
    ("urn:li:ugcPost:123", True),
    ("urn:li:activity:123", True),
    ("7494223989318541312", False),          # bare id — kind unknown
    ("urn:li:nonsense:123", False),
    ("", False),
])
def test_only_recognised_urns_become_links(urn, ok):
    """A bare numeric id has no kind, and the three kinds take different paths.
    Guessing one produces a plausible dead link rather than an obvious failure."""
    post = Post(linkedin_post_id=urn)
    assert (permalink_for(post) is not None) is ok


# -------------------------------------------------------------------- capture --

@pytest.mark.asyncio
async def test_a_reading_records_counts_and_age(db_session, monkeypatch):
    monkeypatch.setattr("app.services.metrics_service.DirectEgress.fetch", _serve())
    user = await _user(db_session)
    post = await _post(db_session, user, published_days_ago=2)

    metric = await capture_post(db_session, post)

    assert metric.reactions == 42
    assert metric.comments == 7
    assert metric.source == "public_page"
    # Stored, not derived at read time, so readings stay comparable across posts
    # published at different times of day.
    assert 47 <= metric.age_hours <= 49


@pytest.mark.asyncio
async def test_impressions_stay_null_without_the_analytics_scope(db_session, monkeypatch):
    monkeypatch.setattr("app.services.metrics_service.DirectEgress.fetch", _serve())
    user = await _user(db_session)
    post = await _post(db_session, user)

    metric = await capture_post(db_session, post)

    assert metric.impressions is None
    assert metric.reposts is None


@pytest.mark.asyncio
async def test_an_authwalled_page_records_nothing_rather_than_zero(db_session, monkeypatch):
    """An authwall reads as zero engagement. Storing it would flatten the series
    with a number that was never measured."""
    monkeypatch.setattr(
        "app.services.metrics_service.DirectEgress.fetch",
        _serve("<html>Join now to see this post</html>"),
    )
    user = await _user(db_session)
    post = await _post(db_session, user)

    assert await capture_post(db_session, post) is None


@pytest.mark.asyncio
async def test_a_fetch_failure_leaves_a_gap_not_an_exception(db_session, monkeypatch):
    async def boom(self, url):
        raise EgressError("blocked")

    monkeypatch.setattr("app.services.metrics_service.DirectEgress.fetch", boom)
    user = await _user(db_session)
    post = await _post(db_session, user)

    assert await capture_post(db_session, post) is None


# ------------------------------------------------------------------ due posts --

@pytest.mark.asyncio
async def test_only_published_posts_inside_the_window_are_due(db_session, monkeypatch):
    monkeypatch.setattr(settings, "metrics_capture_window_days", 30)
    user = await _user(db_session)

    fresh = await _post(db_session, user, urn="urn:li:share:1", published_days_ago=1)
    await _post(db_session, user, urn="urn:li:share:2", published_days_ago=90)
    await _post(db_session, user, urn="urn:li:share:3", status="draft")
    await _post(db_session, user, urn="not-a-urn", published_days_ago=1)

    due = await due_posts(db_session)
    assert [p.id for p in due] == [fresh.id]


@pytest.mark.asyncio
async def test_a_post_measured_recently_is_not_due_again(db_session, monkeypatch):
    monkeypatch.setattr(settings, "metrics_refresh_interval_hours", 24)
    user = await _user(db_session)
    post = await _post(db_session, user)

    db_session.add(PostMetric(post_id=post.id, captured_at=_now() - timedelta(hours=2),
                              reactions=1))
    await db_session.flush()
    assert await due_posts(db_session) == []

    db_session.add(PostMetric(post_id=post.id, captured_at=_now() - timedelta(hours=30),
                              reactions=1))
    await db_session.flush()
    # Still not due: the NEWEST reading is what counts, and that is 2h old.
    assert await due_posts(db_session) == []


@pytest.mark.asyncio
async def test_never_measured_posts_are_taken_first(db_session, monkeypatch):
    monkeypatch.setattr(settings, "metrics_refresh_interval_hours", 24)
    user = await _user(db_session)
    measured = await _post(db_session, user, urn="urn:li:share:1")
    virgin = await _post(db_session, user, urn="urn:li:share:2")

    db_session.add(PostMetric(post_id=measured.id, captured_at=_now() - timedelta(hours=48),
                              reactions=1))
    await db_session.flush()

    due = await due_posts(db_session)
    assert [p.id for p in due] == [virgin.id, measured.id]


# ------------------------------------------------------------- the flood guard --

@pytest.mark.asyncio
async def test_the_daily_cap_bounds_a_run_and_says_so(db_session, monkeypatch):
    monkeypatch.setattr("app.services.metrics_service.DirectEgress.fetch", _serve())
    monkeypatch.setattr(settings, "metrics_daily_capture_cap", 2)
    user = await _user(db_session)
    for i in range(5):
        await _post(db_session, user, urn=f"urn:li:share:{i}")

    summary = await capture_due(db_session)

    assert summary.considered == 5
    assert summary.captured == 2
    # Silently measuring half would read as a run that measured everything.
    assert any("the rest are next time" in n for n in summary.notes)


@pytest.mark.asyncio
async def test_the_cap_counts_what_was_already_captured_today(db_session, monkeypatch):
    """Restart-driven refreshes are the flood risk; the ceiling has to survive
    the process restarting, so it is measured from the table, not from memory."""
    monkeypatch.setattr("app.services.metrics_service.DirectEgress.fetch", _serve())
    monkeypatch.setattr(settings, "metrics_daily_capture_cap", 3)
    user = await _user(db_session)
    seeded = await _post(db_session, user, urn="urn:li:share:seed")
    for i in range(3):
        db_session.add(PostMetric(post_id=seeded.id, captured_at=_now(), reactions=1))
    await db_session.flush()

    summary = await capture_due(db_session)

    assert summary.captured == 0
    assert "daily capture cap" in summary.stopped


@pytest.mark.asyncio
async def test_an_open_circuit_stops_capture_entirely(db_session, monkeypatch):
    """Being blocked is a property of this IP, not of one subsystem."""
    monkeypatch.setattr("app.services.metrics_service.DirectEgress.fetch", _serve())
    monkeypatch.setattr("app.services.metrics_service.fetcher.any_circuit_open", lambda: True)
    user = await _user(db_session)
    await _post(db_session, user)

    summary = await capture_due(db_session)

    assert summary.captured == 0
    assert "cooling down" in summary.stopped


@pytest.mark.asyncio
async def test_capture_can_be_switched_off(db_session, monkeypatch):
    monkeypatch.setattr(settings, "metrics_enabled", False)
    called = AsyncMock()
    monkeypatch.setattr("app.services.metrics_service.DirectEgress.fetch", called)
    user = await _user(db_session)
    await _post(db_session, user)

    summary = await capture_due(db_session)

    assert summary.stopped == "metrics capture is disabled"
    called.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_full_run_stores_one_reading_per_due_post(db_session, monkeypatch):
    monkeypatch.setattr("app.services.metrics_service.DirectEgress.fetch", _serve())
    user = await _user(db_session)
    for i in range(3):
        await _post(db_session, user, urn=f"urn:li:share:{i}")

    summary = await capture_due(db_session)
    rows = (await db_session.execute(select(PostMetric))).scalars().all()

    assert summary.captured == 3
    assert len(rows) == 3
    assert {r.reactions for r in rows} == {42}
