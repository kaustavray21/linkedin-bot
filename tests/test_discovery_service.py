from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from app.core.config import settings
from app.database.models import DiscoveredPost
from app.services.discovery.egress.base import EgressError, FetchResult
from app.services.discovery.providers import DiscoveredCandidate, SearchOutcome
from app.services.discovery.service import purge_expired, purge_post, run_discovery

POST_A = "https://www.linkedin.com/posts/dana_x-activity-111"
POST_B = "https://www.linkedin.com/posts/sam_y-activity-222"

# Counts sit on the social-actions anchors, not in the JSON-LD — see the parser
# fixtures in test_discovery.py for why this shape and not a JSON blob.
PAGE = """
<html><head>
<script type="application/ld+json">
{"@type":"DiscussionForumPosting",
 "articleBody":"I shipped it.\\n\\nTwice.\\n\\nHere is the lesson learned. #BuildInPublic",
 "datePublished":"2026-07-01T10:00:00Z",
 "comment":[],
 "commentCount":0,
 "author":{"name":"Dana Lin"},
 "image":{"url":"https://media.licdn.com/a.jpg"}}
</script></head><body><main><section class="mb-3">
<article class="relative container-lined main-feed-activity-card">
  <div class="flex items-center main-feed-activity-card__social-actions">
    <a data-test-id="social-actions__reactions" data-id="social-actions__reactions"
       aria-label="500 Reactions" data-num-reactions="500">
       <span data-test-id="social-actions__reaction-count">500</span></a>
    <a data-test-id="social-actions__comments" data-num-comments="40">40 Comments</a>
  </div>
</article>
</section></main></body></html>
"""


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class FakeProvider:
    name = "fake"

    def __init__(self, urls):
        self.urls = urls

    async def search(self, keyword, limit, **_options):
        return SearchOutcome(
            provider=self.name,
            candidates=[
                DiscoveredCandidate(
                    post_url=u, snippet="snippet", serp_rank=i + 1, source=self.name
                )
                for i, u in enumerate(self.urls)
            ],
        )


@pytest.fixture
def fake_pipeline(monkeypatch):
    """Stub search and fetch so no network is touched by these tests."""
    fetched: list[str] = []

    async def fake_fetch(url, egress_name=None):
        fetched.append(url)
        return FetchResult(url, 200, PAGE, "html", egress="direct")

    monkeypatch.setattr("app.services.discovery.service.fetcher.fetch", fake_fetch)
    return fetched


@pytest.mark.asyncio
async def test_discovery_stores_parsed_posts(db_session, monkeypatch, fake_pipeline):
    monkeypatch.setattr(
        "app.services.discovery.service.get_provider",
        lambda name: FakeProvider([POST_A, POST_B]),
    )

    job = await run_discovery(db_session, keyword="shipping", limit=5)

    assert job.status == "success"
    assert job.found_count == 2
    assert job.fetched_count == 2

    posts = (await db_session.execute(select(DiscoveredPost))).scalars().all()
    assert len(posts) == 2
    assert posts[0].author_name == "Dana Lin"
    assert posts[0].reactions == 500
    assert posts[0].metrics_source == "measured"
    assert posts[0].engagement_score > 0


@pytest.mark.asyncio
async def test_layout_skeleton_is_captured_at_fetch_time(db_session, monkeypatch, fake_pipeline):
    """The skeleton must be stored up front — it has to outlive the raw text
    when the post is later purged."""
    monkeypatch.setattr(
        "app.services.discovery.service.get_provider", lambda name: FakeProvider([POST_A])
    )
    await run_discovery(db_session, keyword="shipping", limit=1)

    post = (await db_session.execute(select(DiscoveredPost))).scalar_one()
    assert post.layout_skeleton is not None
    assert post.layout_skeleton["blocks"]


@pytest.mark.asyncio
async def test_known_posts_are_not_refetched(db_session, monkeypatch, fake_pipeline):
    """Refetching a cached post would spend daily budget that could have gone to
    a post we have never seen."""
    monkeypatch.setattr(
        "app.services.discovery.service.get_provider", lambda name: FakeProvider([POST_A])
    )

    await run_discovery(db_session, keyword="shipping", limit=5)
    assert len(fake_pipeline) == 1

    await run_discovery(db_session, keyword="shipping", limit=5)
    assert len(fake_pipeline) == 1          # no second fetch

    posts = (await db_session.execute(select(DiscoveredPost))).scalars().all()
    assert len(posts) == 1
    assert posts[0].query_overlap == 2      # but the repeat sighting is recorded


@pytest.mark.asyncio
async def test_run_halts_and_reports_partial_when_budget_runs_out(db_session, monkeypatch):
    monkeypatch.setattr(
        "app.services.discovery.service.get_provider",
        lambda name: FakeProvider([POST_A, POST_B]),
    )

    calls = {"n": 0}

    async def capped_fetch(url, egress_name=None):
        calls["n"] += 1
        if calls["n"] > 1:
            raise EgressError("Daily fetch cap reached (40).")
        return FetchResult(url, 200, PAGE, "html")

    monkeypatch.setattr("app.services.discovery.service.fetcher.fetch", capped_fetch)

    job = await run_discovery(db_session, keyword="shipping", limit=5)

    assert job.status == "partial"
    assert "Daily fetch cap" in job.error
    assert job.fetched_count == 1


@pytest.mark.asyncio
async def test_all_pages_unreadable_is_partial_not_success(db_session, monkeypatch):
    """Reporting success while extracting nothing would hide a broken parser."""
    monkeypatch.setattr(
        "app.services.discovery.service.get_provider", lambda name: FakeProvider([POST_A])
    )

    async def authwall(url, egress_name=None):
        return FetchResult(url, 200, "<html>Join now to see this post</html>", "html")

    monkeypatch.setattr("app.services.discovery.service.fetcher.fetch", authwall)

    job = await run_discovery(db_session, keyword="shipping", limit=1)
    assert job.status == "partial"
    assert "could not extract" in job.error


@pytest.mark.asyncio
async def test_search_failure_is_reported_as_failed(db_session, monkeypatch):
    class DeadProvider:
        name = "dead"

        async def search(self, keyword, limit, **_options):
            return SearchOutcome(provider=self.name, error="search backend unreachable")

    monkeypatch.setattr(
        "app.services.discovery.service.get_provider", lambda name: DeadProvider()
    )

    job = await run_discovery(db_session, keyword="shipping", limit=5)
    assert job.status == "failed"
    assert "unreachable" in job.error


# --------------------------------------------------------------- retention --

@pytest.mark.asyncio
async def test_purge_drops_content_but_keeps_the_skeleton(db_session, monkeypatch, fake_pipeline):
    monkeypatch.setattr(
        "app.services.discovery.service.get_provider", lambda name: FakeProvider([POST_A])
    )
    await run_discovery(db_session, keyword="shipping", limit=1)

    post = (await db_session.execute(select(DiscoveredPost))).scalar_one()
    skeleton_before = post.layout_skeleton

    await purge_post(db_session, post)
    await db_session.flush()

    assert post.content_text is None
    assert post.snippet is None
    assert post.image_url is None
    assert post.purged_at is not None
    # The structure carries no wording from the source, so it can safely stay —
    # and keeps drafts already built from it reproducible.
    assert post.layout_skeleton == skeleton_before
    assert post.post_url == POST_A


@pytest.mark.asyncio
async def test_expired_posts_are_purged_and_fresh_ones_left_alone(db_session):
    stale = DiscoveredPost(
        keyword="k", source="fake", post_url=POST_A, content_text="old text",
        expires_at=_now() - timedelta(days=1), fetched_at=_now(),
    )
    fresh = DiscoveredPost(
        keyword="k", source="fake", post_url=POST_B, content_text="new text",
        expires_at=_now() + timedelta(days=30), fetched_at=_now(),
    )
    db_session.add_all([stale, fresh])
    await db_session.flush()

    purged = await purge_expired(db_session)
    await db_session.flush()

    assert purged == 1
    assert stale.content_text is None
    assert fresh.content_text == "new text"


@pytest.mark.asyncio
async def test_purge_expired_is_idempotent(db_session):
    stale = DiscoveredPost(
        keyword="k", source="fake", post_url=POST_A, content_text="old",
        expires_at=_now() - timedelta(days=1), fetched_at=_now(),
    )
    db_session.add(stale)
    await db_session.flush()

    assert await purge_expired(db_session) == 1
    await db_session.flush()
    assert await purge_expired(db_session) == 0      # already purged, not re-counted


@pytest.mark.asyncio
async def test_retention_window_comes_from_config(db_session, monkeypatch, fake_pipeline):
    monkeypatch.setattr(settings, "discovery_retention_days", 7)
    monkeypatch.setattr(
        "app.services.discovery.service.get_provider", lambda name: FakeProvider([POST_A])
    )
    await run_discovery(db_session, keyword="shipping", limit=1)

    post = (await db_session.execute(select(DiscoveredPost))).scalar_one()
    assert 6 <= (post.expires_at - _now()).days <= 7


# ------------------------------------------------------------ classification --

def _enable_classification(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "discovery_classify", True)


async def _seed_types(db, *slugs):
    from app.database.models import PostType

    for slug in slugs:
        db.add(PostType(slug=slug, label=slug.title(), description=f"The {slug} type",
                        origin="seed"))
    await db.flush()


@pytest.mark.asyncio
async def test_every_stored_post_gets_classified(db_session, monkeypatch, fake_pipeline):
    from app.services.post_type_service import TypeProposal

    _enable_classification(monkeypatch)
    await _seed_types(db_session, "story")
    monkeypatch.setattr(
        "app.services.discovery.service.get_provider",
        lambda name: FakeProvider([POST_A, POST_B]),
    )
    monkeypatch.setattr(
        "app.services.discovery.service.propose_type",
        AsyncMock(return_value=TypeProposal(existing_slug="story")),
    )

    await run_discovery(db_session, keyword="shipping", limit=5)

    posts = (await db_session.execute(select(DiscoveredPost))).scalars().all()
    assert len(posts) == 2
    assert {p.post_type_slug for p in posts} == {"story"}


@pytest.mark.asyncio
async def test_a_refused_classification_still_stores_the_post(db_session, monkeypatch, fake_pipeline):
    """Classification is enrichment. Losing the post — the expensive thing on
    this path — because a model was unreachable would be the wrong trade."""
    from app.services.post_type_service import TypeProposal

    _enable_classification(monkeypatch)
    monkeypatch.setattr(
        "app.services.discovery.service.get_provider",
        lambda name: FakeProvider([POST_A]),
    )
    monkeypatch.setattr(
        "app.services.discovery.service.propose_type",
        AsyncMock(return_value=TypeProposal(refused="the classifier was unavailable")),
    )

    job = await run_discovery(db_session, keyword="shipping", limit=5)

    assert job.status == "success"
    post = (await db_session.execute(select(DiscoveredPost))).scalar_one()
    assert post.post_type_slug is None


@pytest.mark.asyncio
async def test_a_classifier_crash_does_not_take_the_run_down(db_session, monkeypatch, fake_pipeline):
    _enable_classification(monkeypatch)
    monkeypatch.setattr(
        "app.services.discovery.service.get_provider",
        lambda name: FakeProvider([POST_A]),
    )
    monkeypatch.setattr(
        "app.services.discovery.service.propose_type",
        AsyncMock(side_effect=RuntimeError("model exploded")),
    )

    job = await run_discovery(db_session, keyword="shipping", limit=5)

    assert job.status == "success"
    assert job.fetched_count == 1
    post = (await db_session.execute(select(DiscoveredPost))).scalar_one()
    assert post.post_type_slug is None


@pytest.mark.asyncio
async def test_classification_runs_before_any_of_the_writing(db_session, monkeypatch, fake_pipeline):
    """The wave proposes in parallel, then resolves serially. If a proposal were
    awaited inside the write loop instead, the model calls would serialise and a
    30-post search would take minutes rather than seconds."""
    from app.services.post_type_service import TypeProposal

    _enable_classification(monkeypatch)
    await _seed_types(db_session, "story")
    order: list[str] = []

    async def _propose(text, taxonomy, ai_service=None):
        order.append("propose")
        return TypeProposal(existing_slug="story")

    real_resolve = __import__(
        "app.services.post_type_service", fromlist=["resolve_proposal"]
    ).resolve_proposal

    async def _resolve(db, proposal):
        order.append("resolve")
        return await real_resolve(db, proposal)

    monkeypatch.setattr("app.services.discovery.service.get_provider",
                        lambda name: FakeProvider([POST_A, POST_B]))
    monkeypatch.setattr("app.services.discovery.service.propose_type", _propose)
    monkeypatch.setattr("app.services.discovery.service.resolve_proposal", _resolve)

    await run_discovery(db_session, keyword="shipping", limit=5)

    # Both proposals are made before either resolution touches the session.
    assert order == ["propose", "propose", "resolve", "resolve"]


@pytest.mark.asyncio
async def test_classification_off_stores_posts_with_no_type(db_session, monkeypatch, fake_pipeline):
    from app.core.config import settings

    monkeypatch.setattr(settings, "discovery_classify", False)
    monkeypatch.setattr("app.services.discovery.service.get_provider",
                        lambda name: FakeProvider([POST_A]))
    called = AsyncMock()
    monkeypatch.setattr("app.services.discovery.service.propose_type", called)

    await run_discovery(db_session, keyword="shipping", limit=5)

    called.assert_not_awaited()
    post = (await db_session.execute(select(DiscoveredPost))).scalar_one()
    assert post.post_type_slug is None
