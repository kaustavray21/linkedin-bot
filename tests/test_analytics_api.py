from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.core.config import settings
from app.database.models import Post, PostMetric, User


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


async def _published(db, user, *, urn="urn:li:share:1", days_ago=1):
    post = Post(user_id=user.id, content="First line\nsecond line", status="published",
                linkedin_post_id=urn, published_time=_now() - timedelta(days=days_ago))
    db.add(post)
    await db.flush()
    return post


@pytest.mark.asyncio
async def test_outcomes_report_the_basis_they_rest_on(async_client, db_session, monkeypatch):
    monkeypatch.setattr(settings, "outcome_min_samples", 3)
    user = User(linkedin_member_id="me")
    db_session.add(user)
    await db_session.flush()

    subject = await _published(db_session, user)
    db_session.add(PostMetric(post_id=subject.id, captured_at=_now(), age_hours=24, reactions=20))
    for i in range(3):
        other = await _published(db_session, user, urn=f"urn:li:share:{i + 2}")
        db_session.add(PostMetric(post_id=other.id, captured_at=_now(), age_hours=24, reactions=10))
    await db_session.flush()

    body = (await async_client.get(f"/analytics/outcomes?user_id={user.id}")).json()
    row = next(r for r in body if r["post_id"] == subject.id)

    assert row["basis"] == "measured"
    assert row["ratio"] == 2.0
    assert row["sample_size"] == 3
    assert row["preview"] == "First line"
    assert row["permalink"].startswith("https://www.linkedin.com/feed/update/")


@pytest.mark.asyncio
async def test_an_unmeasured_post_says_so_rather_than_reporting_zero(async_client, db_session):
    user = User(linkedin_member_id="me2")
    db_session.add(user)
    await db_session.flush()
    await _published(db_session, user)

    body = (await async_client.get(f"/analytics/outcomes?user_id={user.id}")).json()

    assert body[0]["basis"] == "not_measured"
    assert body[0]["engagement"] is None
    assert body[0]["ratio"] is None


@pytest.mark.asyncio
async def test_the_series_returns_every_reading_oldest_first(async_client, db_session):
    user = User(linkedin_member_id="me3")
    db_session.add(user)
    await db_session.flush()
    post = await _published(db_session, user)

    for hours, reactions in ((24, 5), (72, 9), (168, 11)):
        db_session.add(PostMetric(
            post_id=post.id, captured_at=_now() - timedelta(hours=200 - hours),
            age_hours=hours, reactions=reactions,
        ))
    await db_session.flush()

    body = (await async_client.get(f"/analytics/posts/{post.id}/series")).json()

    # The series, not the latest number: a post that died at 24h and one still
    # climbing at day seven are identical at a single point.
    assert [p["age_hours"] for p in body] == [24, 72, 168]
    assert [p["reactions"] for p in body] == [5, 9, 11]


@pytest.mark.asyncio
async def test_manual_capture_reports_when_it_was_stopped(async_client, db_session, monkeypatch):
    """Pressing refresh repeatedly must not become a request storm — the same
    bounds the scheduler runs under still apply, and the reason is reported."""
    monkeypatch.setattr(settings, "metrics_enabled", False)

    body = (await async_client.post("/analytics/capture")).json()

    assert body["captured"] == 0
    assert body["stopped"] == "metrics capture is disabled"
