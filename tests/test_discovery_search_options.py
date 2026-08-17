"""Search options added in P2: hashtags, recency window, and the author link.

The hashtag and recency values have to survive a hop that is easy to miss —
`POST /discovery/search` only *queues* a job, and a background task re-reads the
row to run it. Anything not persisted on `discovery_jobs` is silently dropped
between the two, and the search just quietly ignores what you asked for.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.database.models import DiscoveredPost, DiscoveryJob
from app.services.discovery.providers import build_queries, normalise_hashtags


# ------------------------------------------------------------- normalising --


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("#ai", ["#ai"]),
        ("ai", ["#ai"]),
        ("#ai, buildinpublic", ["#ai", "#buildinpublic"]),
        ("#AI  #ai", ["#AI"]),                    # case-insensitive dedupe
        (["#ai", "startups"], ["#ai", "#startups"]),
        ("", []),
        (None, []),
        ("  ,, # ", []),                          # nothing usable
    ],
)
def test_hashtags_normalise_from_however_they_were_typed(raw, expected):
    assert normalise_hashtags(raw) == expected


def test_hashtags_become_their_own_query_angle():
    """Folding a tag into every query narrows results; a tagged post often does
    not repeat the topic words at all."""
    queries = build_queries("shipping", ["#BuildInPublic"])

    assert any(q == 'site:linkedin.com/posts "#BuildInPublic"' for q in queries), (
        "the tag must be searchable on its own"
    )
    assert any("shipping" in q and "#BuildInPublic" in q for q in queries), (
        "and in combination with the topic"
    )
    # The plain topic queries survive untouched.
    assert 'site:linkedin.com/posts "shipping"' in queries


def test_query_count_is_bounded_by_tag_cap():
    """Each tag adds two queries and every query is a live search request."""
    many = [f"#tag{i}" for i in range(10)]
    assert len(build_queries("topic", many)) == 3 + 2 * 3


# ------------------------------------------------------ persisted on the job --


@pytest.mark.asyncio
async def test_search_options_are_persisted_for_the_background_run(
    async_client, db_session, monkeypatch
):
    monkeypatch.setattr("app.api.discovery.start_job", lambda _id: None)

    response = await async_client.post(
        "/discovery/search",
        json={
            "keyword": "shipping",
            "hashtags": "#ai, buildinpublic",
            "timelimit": "w",
            "limit": 30,
        },
    )
    assert response.status_code == 202

    job = (
        await db_session.execute(
            select(DiscoveryJob).where(DiscoveryJob.id == response.json()["id"])
        )
    ).scalar_one()

    assert job.hashtags == ["#ai", "#buildinpublic"]
    assert job.timelimit == "w"
    assert job.requested_count == 30


@pytest.mark.asyncio
async def test_an_unknown_recency_window_is_dropped_not_forwarded(
    async_client, db_session, monkeypatch
):
    """ddgs accepts d|w|m|y. Passing anything else through would either error
    at search time or be ignored, both of which look like the filter working."""
    monkeypatch.setattr("app.api.discovery.start_job", lambda _id: None)

    response = await async_client.post(
        "/discovery/search", json={"keyword": "x", "timelimit": "decade"}
    )
    job = (
        await db_session.execute(
            select(DiscoveryJob).where(DiscoveryJob.id == response.json()["id"])
        )
    ).scalar_one()
    assert job.timelimit is None


@pytest.mark.asyncio
async def test_hashtags_alone_are_a_valid_search(async_client, monkeypatch):
    monkeypatch.setattr("app.api.discovery.start_job", lambda _id: None)
    response = await async_client.post(
        "/discovery/search", json={"keyword": "", "hashtags": "#buildinpublic"}
    )
    assert response.status_code == 202


@pytest.mark.asyncio
async def test_an_empty_search_is_refused(async_client):
    response = await async_client.post("/discovery/search", json={"keyword": "   "})
    assert response.status_code == 400


# --------------------------------------------------------- the author link --


@pytest.mark.asyncio
async def test_author_profile_url_reaches_the_client(async_client, db_session):
    """It was stored but never serialised, so a card could not link to the
    author despite having the URL."""
    db_session.add(
        DiscoveredPost(
            keyword="topic",
            source="ddg",
            post_url="https://www.linkedin.com/posts/abc",
            author_name="A Person",
            author_profile_url="https://www.linkedin.com/in/a-person",
            content_text="body",
            metrics_source="inferred",
        )
    )
    await db_session.flush()

    response = await async_client.get("/discovery/posts")
    assert response.status_code == 200
    assert response.json()[0]["author_profile_url"] == (
        "https://www.linkedin.com/in/a-person"
    )
