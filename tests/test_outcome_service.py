from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.core.config import settings
from app.database.models import Post, PostMetric, User
from app.services.outcome_service import (
    MEASURED,
    NOT_ENOUGH_DATA,
    NOT_MEASURED,
    outcome_for,
    outcomes_for_user,
    rolling_median,
)


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


async def _user(db) -> User:
    user = User(linkedin_member_id="me")
    db.add(user)
    await db.flush()
    return user


async def _post(db, user, *, days_ago=1) -> Post:
    post = Post(user_id=user.id, content="x", status="published",
                linkedin_post_id="urn:li:share:1",
                published_time=_now() - timedelta(days=days_ago))
    db.add(post)
    await db.flush()
    return post


async def _reading(db, post, *, age_hours, reactions, comments=None):
    db.add(PostMetric(post_id=post.id, captured_at=_now(), age_hours=age_hours,
                      reactions=reactions, comments=comments))
    await db.flush()


@pytest.fixture(autouse=True)
def _thresholds(monkeypatch):
    monkeypatch.setattr(settings, "outcome_min_samples", 3)
    monkeypatch.setattr(settings, "outcome_age_tolerance_hours", 36)


# ------------------------------------------------------------------- the ratio --

@pytest.mark.asyncio
async def test_a_post_is_measured_against_your_own_median(db_session):
    user = await _user(db_session)
    subject = await _post(db_session, user)
    await _reading(db_session, subject, age_hours=24, reactions=20)

    for reactions in (5, 10, 15):
        other = await _post(db_session, user)
        await _reading(db_session, other, age_hours=24, reactions=reactions)

    outcome = await outcome_for(db_session, subject)

    assert outcome.basis == MEASURED
    assert outcome.median == 10
    assert outcome.ratio == 2.0
    assert outcome.sample_size == 3


@pytest.mark.asyncio
async def test_the_post_itself_is_excluded_from_its_own_baseline(db_session):
    """Including it would drag the median toward the number being judged."""
    user = await _user(db_session)
    subject = await _post(db_session, user)
    await _reading(db_session, subject, age_hours=24, reactions=1000)

    for reactions in (5, 10, 15):
        other = await _post(db_session, user)
        await _reading(db_session, other, age_hours=24, reactions=reactions)

    outcome = await outcome_for(db_session, subject)
    assert outcome.median == 10


# --------------------------------------------------------------- comparability --

@pytest.mark.asyncio
async def test_only_readings_at_a_comparable_age_form_the_median(db_session):
    """A post at 24h and one at seven days answer different questions."""
    user = await _user(db_session)
    subject = await _post(db_session, user)
    await _reading(db_session, subject, age_hours=24, reactions=20)

    for reactions in (100, 200, 300):
        other = await _post(db_session, user)
        await _reading(db_session, other, age_hours=168, reactions=reactions)   # 7 days

    outcome = await outcome_for(db_session, subject)

    assert outcome.basis == NOT_ENOUGH_DATA
    assert outcome.sample_size == 0
    assert outcome.ratio is None


@pytest.mark.asyncio
async def test_the_nearest_reading_in_range_is_the_one_used(db_session):
    user = await _user(db_session)
    subject = await _post(db_session, user)
    await _reading(db_session, subject, age_hours=24, reactions=20)

    for _ in range(3):
        other = await _post(db_session, user)
        await _reading(db_session, other, age_hours=1, reactions=2)      # too early
        await _reading(db_session, other, age_hours=26, reactions=10)    # nearest
        await _reading(db_session, other, age_hours=200, reactions=99)   # too late

    outcome = await outcome_for(db_session, subject)
    assert outcome.median == 10


# ------------------------------------------------------------ honest abstention --

@pytest.mark.asyncio
async def test_too_few_comparable_posts_reports_not_enough_data(db_session):
    user = await _user(db_session)
    subject = await _post(db_session, user)
    await _reading(db_session, subject, age_hours=24, reactions=20)

    for reactions in (5, 10):          # two — below the minimum of three
        other = await _post(db_session, user)
        await _reading(db_session, other, age_hours=24, reactions=reactions)

    outcome = await outcome_for(db_session, subject)

    assert outcome.basis == NOT_ENOUGH_DATA
    assert outcome.ratio is None
    assert outcome.median is None
    # "We looked and found two" is different information from "we did not look".
    assert outcome.sample_size == 2
    assert outcome.engagement == 20


@pytest.mark.asyncio
async def test_a_post_with_no_readings_is_not_measured(db_session):
    user = await _user(db_session)
    subject = await _post(db_session, user)

    outcome = await outcome_for(db_session, subject)

    assert outcome.basis == NOT_MEASURED
    assert outcome.engagement is None
    assert outcome.ratio is None


@pytest.mark.asyncio
async def test_an_unreadable_reading_does_not_count_as_zero(db_session):
    user = await _user(db_session)
    subject = await _post(db_session, user)
    await _reading(db_session, subject, age_hours=24, reactions=None)

    outcome = await outcome_for(db_session, subject)
    assert outcome.basis == NOT_MEASURED


@pytest.mark.asyncio
async def test_a_zero_median_yields_no_ratio_but_keeps_the_engagement(db_session):
    """Dividing by it would report infinity. Nothing you posted got a reaction
    at that age is a real outcome, and the engagement still stands on its own."""
    user = await _user(db_session)
    subject = await _post(db_session, user)
    await _reading(db_session, subject, age_hours=24, reactions=7)

    for _ in range(3):
        other = await _post(db_session, user)
        await _reading(db_session, other, age_hours=24, reactions=0)

    outcome = await outcome_for(db_session, subject)

    assert outcome.median == 0
    assert outcome.ratio is None
    assert outcome.engagement == 7
    assert outcome.basis == MEASURED


@pytest.mark.asyncio
async def test_engagement_is_reactions_alone_with_comments_alongside(db_session):
    """Folding comments in would mean treating an absent count as zero — the
    trap the ranking exists to avoid — or a composite that shifts meaning
    between posts."""
    user = await _user(db_session)
    subject = await _post(db_session, user)
    await _reading(db_session, subject, age_hours=24, reactions=20, comments=None)

    for _ in range(3):
        other = await _post(db_session, user)
        await _reading(db_session, other, age_hours=24, reactions=10, comments=5)

    outcome = await outcome_for(db_session, subject)

    assert outcome.engagement == 20
    assert outcome.comments is None
    assert outcome.ratio == 2.0


@pytest.mark.asyncio
async def test_another_users_posts_never_enter_the_baseline(db_session):
    mine = await _user(db_session)
    theirs = User(linkedin_member_id="someone-else")
    db_session.add(theirs)
    await db_session.flush()

    subject = await _post(db_session, mine)
    await _reading(db_session, subject, age_hours=24, reactions=20)
    for _ in range(3):
        other = await _post(db_session, theirs)
        await _reading(db_session, other, age_hours=24, reactions=10)

    median, sample = await rolling_median(db_session, mine.id, 24, subject.id)
    assert median is None
    assert sample == 0


@pytest.mark.asyncio
async def test_every_published_post_gets_an_outcome(db_session):
    user = await _user(db_session)
    for i in range(4):
        post = await _post(db_session, user, days_ago=i + 1)
        await _reading(db_session, post, age_hours=24, reactions=10 + i)

    outcomes = await outcomes_for_user(db_session, user.id)

    assert len(outcomes) == 4
    assert all(o.basis == MEASURED for o in outcomes)
