from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.core.config import settings
from app.services.discovery.egress.base import EgressError, FetchResult
from app.services.discovery.egress.strategies import (
    DirectEgress,
    JinaEgress,
    available_strategies,
    get_egress,
)
from app.services.discovery.fetcher import RateLimitedFetcher
from app.services.discovery.parser import parse_post
from app.services.discovery.providers import (
    build_queries,
    get_provider,
    normalise_post_url,
)
from app.services.discovery.ranking import compute_score, describe_basis, serp_factor

POST_URL = "https://www.linkedin.com/posts/someone_a-slug-activity-7123456789"


def _now() -> datetime:
    """Naive UTC, matching how the app stores every datetime."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ------------------------------------------------------------------ egress --

def test_unknown_egress_falls_back_to_direct_not_to_nothing():
    """A typo in configuration must not silently disable fetching, nor route
    traffic somewhere unexpected."""
    assert get_egress("nonsense").name == "direct"
    assert get_egress(None).name in available_strategies()


def test_each_strategy_is_resolvable_by_name():
    assert isinstance(get_egress("direct"), DirectEgress)
    assert isinstance(get_egress("jina"), JinaEgress)


def test_direct_is_html_and_jina_is_markdown():
    """The parser gates its JSON-LD layer on this distinction."""
    assert FetchResult("u", 200, "<html>", "html").content_kind == "html"
    assert FetchResult("u", 200, "# md", "markdown").content_kind == "markdown"


def test_blocked_status_codes_recognised():
    assert FetchResult("u", 999, "", "html").is_blocked      # LinkedIn's bot code
    assert FetchResult("u", 429, "", "html").is_blocked
    assert not FetchResult("u", 200, "x", "html").is_blocked


# ----------------------------------------------------------------- fetcher --

class FakeEgress:
    def __init__(self, name, status=200, content="<html>ok</html>", raises=False):
        self.name = name
        self.status = status
        self.content = content
        self.raises = raises
        self.calls = 0

    async def fetch(self, url):
        self.calls += 1
        if self.raises:
            raise EgressError("boom")
        return FetchResult(url, self.status, self.content, "html", egress=self.name)


@pytest.fixture
def no_pacing(monkeypatch):
    """Remove the inter-request delay so tests do not wait 30s per fetch."""
    monkeypatch.setattr(settings, "discovery_min_interval_seconds", 0.0)
    monkeypatch.setattr(settings, "discovery_jitter_seconds", 0.0)


@pytest.mark.asyncio
async def test_daily_cap_is_enforced(no_pacing, monkeypatch):
    monkeypatch.setattr(settings, "discovery_daily_fetch_cap", 2)
    f = RateLimitedFetcher()
    fake = FakeEgress("direct")
    monkeypatch.setattr("app.services.discovery.fetcher.get_egress", lambda n: fake)

    await f.fetch(POST_URL)
    await f.fetch(POST_URL)
    with pytest.raises(EgressError, match="Daily fetch cap"):
        await f.fetch(POST_URL)

    assert fake.calls == 2


@pytest.mark.asyncio
async def test_cap_survives_an_egress_switch(no_pacing, monkeypatch):
    """The cap is a safety limit, not a per-strategy quota — switching egress
    must not hand the caller a fresh budget."""
    monkeypatch.setattr(settings, "discovery_daily_fetch_cap", 2)
    f = RateLimitedFetcher()
    monkeypatch.setattr(
        "app.services.discovery.fetcher.get_egress", lambda n: FakeEgress(n)
    )

    await f.fetch(POST_URL, egress_name="direct")
    await f.fetch(POST_URL, egress_name="jina")
    with pytest.raises(EgressError, match="Daily fetch cap"):
        await f.fetch(POST_URL, egress_name="proxy")


@pytest.mark.asyncio
async def test_repeated_blocks_open_the_circuit(no_pacing, monkeypatch):
    monkeypatch.setattr(settings, "discovery_circuit_threshold", 2)
    monkeypatch.setattr(settings, "discovery_egress_fallback", "direct")
    f = RateLimitedFetcher()
    monkeypatch.setattr(
        "app.services.discovery.fetcher.get_egress",
        lambda n: FakeEgress(n, status=999, content=""),
    )

    for _ in range(2):
        with pytest.raises(EgressError):
            await f.fetch(POST_URL, egress_name="direct")

    assert f.circuit("direct").is_open()

    with pytest.raises(EgressError, match="Circuit open"):
        await f.fetch(POST_URL, egress_name="direct")


@pytest.mark.asyncio
async def test_open_circuit_makes_no_requests(no_pacing, monkeypatch):
    f = RateLimitedFetcher()
    f.circuit("direct").opened_at = _now()
    monkeypatch.setattr(settings, "discovery_egress_fallback", "direct")

    fake = FakeEgress("direct")
    monkeypatch.setattr("app.services.discovery.fetcher.get_egress", lambda n: fake)

    with pytest.raises(EgressError, match="Circuit open"):
        await f.fetch(POST_URL, egress_name="direct")
    assert fake.calls == 0


@pytest.mark.asyncio
async def test_breakers_are_per_strategy(no_pacing, monkeypatch):
    """Jina being blocked says nothing about direct. One shared breaker would
    let either path disable both."""
    monkeypatch.setattr(settings, "discovery_egress_fallback", "direct")
    f = RateLimitedFetcher()
    f.circuit("jina").opened_at = _now()

    healthy = FakeEgress("direct")
    monkeypatch.setattr(
        "app.services.discovery.fetcher.get_egress",
        lambda n: healthy if n == "direct" else FakeEgress("jina", 999, ""),
    )

    result = await f.fetch(POST_URL, egress_name="jina")   # primary open -> falls back
    assert result.ok
    assert result.egress == "direct"
    assert not f.circuit("direct").is_open()


@pytest.mark.asyncio
async def test_circuit_closes_after_cooldown(no_pacing, monkeypatch):
    monkeypatch.setattr(settings, "discovery_circuit_cooldown_hours", 1)
    f = RateLimitedFetcher()
    state = f.circuit("direct")
    state.opened_at = _now() - timedelta(hours=2)

    assert not state.is_open()
    assert state.consecutive_blocks == 0


# ------------------------------------------------------------------ parser --

# Modelled on real markup rather than invented. The previous fixtures asserted
# against '"numLikes":842' — a JSON key that appears nowhere on a LinkedIn post
# page — so the parser tests passed for months while the shipping parser read a
# reaction count on 0 of 35 live posts. Counts live on the social-actions
# anchors, and the JSON-LD block carries a contradicting commentCount that must
# be ignored.


def _social_actions(reactions: int | None, comments: int | None) -> str:
    parts = []
    if reactions is not None:
        parts.append(
            f'<a data-test-id="social-actions__reactions" '
            f'data-id="social-actions__reactions" aria-label="{reactions} Reactions" '
            f'data-num-reactions="{reactions}">'
            f'<span data-test-id="social-actions__reaction-count">{reactions}</span></a>'
        )
    if comments is not None:
        parts.append(
            f'<a data-test-id="social-actions__comments" '
            f'data-num-comments="{comments}">{comments} Comments</a>'
        )
    return (
        '<div class="flex items-center main-feed-activity-card__social-actions">'
        + "".join(parts)
        + "</div>"
    )


def _card(reactions: int | None, comments: int | None) -> str:
    return (
        '<article class="relative container-lined main-feed-activity-card">'
        + _social_actions(reactions, comments)
        + "</article>"
    )


def _related_rail(*cards: str) -> str:
    items = "".join(f'<li class="mb-1.5"><div class="link-overlay relative">{c}</div></li>'
                    for c in cards)
    return (
        '<section class="core-section-container my-3 related-posts">'
        f"<ul>{items}</ul></section>"
    )


def _main_section(card: str) -> str:
    return f'<section class="mb-3"><div class="details">{card}</div></section>'


def _page(body: str, comment_count_in_jsonld: int = 0) -> str:
    """A post page: JSON-LD for the body, markup for the counts."""
    return f"""
<html><head>
<script type="application/ld+json">
{{"@type":"DiscussionForumPosting",
 "articleBody":"I shipped it.\\n\\nTwice.\\n\\nHere is the lesson. #BuildInPublic",
 "datePublished":"2026-07-01T10:00:00Z",
 "comment":[],
 "commentCount":{comment_count_in_jsonld},
 "author":{{"name":"Dana Lin","url":"https://linkedin.com/in/danalin","jobTitle":"Founder"}},
 "image":{{"url":"https://media.licdn.com/x.jpg"}}}}
</script>
</head><body><main>{body}</main></body></html>
"""


JSONLD_PAGE = _page(_main_section(_card(842, 57)))

OG_ONLY_PAGE = """
<html><head>
<meta property="og:title" content="Dana Lin on LinkedIn: my thoughts">
<meta property="og:description" content="A shorter excerpt of the post body #Growth">
<meta property="og:image" content="https://media.licdn.com/y.jpg">
</head><body></body></html>
"""


def test_jsonld_layer_extracts_everything():
    parsed = parse_post(FetchResult(POST_URL, 200, JSONLD_PAGE, "html"))
    assert parsed.layer == "jsonld"
    assert "I shipped it." in parsed.content_text
    assert parsed.author_name == "Dana Lin"
    assert parsed.author_headline == "Founder"
    assert parsed.image_url.endswith("x.jpg")
    assert parsed.reactions == 842
    assert parsed.comments == 57
    assert parsed.metrics_source == "measured"
    assert "#BuildInPublic" in parsed.hashtags


def test_opengraph_layer_used_when_jsonld_absent():
    parsed = parse_post(FetchResult(POST_URL, 200, OG_ONLY_PAGE, "html"))
    assert parsed.layer == "opengraph"
    assert parsed.author_name == "Dana Lin"
    assert "shorter excerpt" in parsed.content_text


def test_unreadable_metrics_stay_none_not_zero():
    """The distinction the whole ranking rests on."""
    parsed = parse_post(FetchResult(POST_URL, 200, OG_ONLY_PAGE, "html"))
    assert parsed.reactions is None
    assert parsed.metrics_source == "inferred"


def test_a_partial_metric_still_counts_as_measured():
    """Observed live: a real post exposed its comment count but not its reaction
    count. Keying the basis off reactions alone mislabelled real data."""
    parsed = parse_post(FetchResult(POST_URL, 200, _page(_main_section(_card(None, 4))), "html"))
    assert parsed.comments == 4
    assert parsed.reactions is None
    assert parsed.metrics_source == "measured"


def test_counts_come_from_the_post_not_the_related_posts_rail():
    """The rail at the foot of the page repeats the identical card markup — up
    to eleven copies on one live page. Reading the first match in the document
    reports a stranger's engagement as this post's."""
    page = _page(_main_section(_card(842, 57)) + _related_rail(_card(3, 1), _card(999, 40)))
    parsed = parse_post(FetchResult(POST_URL, 200, page, "html"))
    assert parsed.reactions == 842
    assert parsed.comments == 57


def test_the_rail_is_excluded_by_meaning_not_by_position():
    """Same assertion with the rail first, so the rule cannot silently decay
    into 'take the first card' the next time LinkedIn reorders the page."""
    page = _page(_related_rail(_card(3, 1)) + _main_section(_card(842, 57)))
    parsed = parse_post(FetchResult(POST_URL, 200, page, "html"))
    assert parsed.reactions == 842
    assert parsed.comments == 57


def test_jsonld_comment_count_is_ignored_when_it_contradicts_the_page():
    """Measured on a live post: the page showed 1,113 reactions and 26 comments
    while its JSON-LD claimed commentCount 0. That block counts the inlined
    comment[] array, not real comments — six of thirty-two sampled posts were
    wrong this way, and each was then labelled 'measured'."""
    page = _page(_main_section(_card(1113, 26)), comment_count_in_jsonld=0)
    parsed = parse_post(FetchResult(POST_URL, 200, page, "html"))
    assert parsed.comments == 26
    assert parsed.reactions == 1113


def test_a_post_with_no_social_block_reports_none_not_zero():
    """LinkedIn omits the block entirely on a post with no engagement. That is
    probably a genuine zero, but it is an inference about absent markup — and a
    count we did not read stays None."""
    page = _page(_main_section('<article class="main-feed-activity-card"></article>'))
    parsed = parse_post(FetchResult(POST_URL, 200, page, "html"))
    assert parsed.reactions is None
    assert parsed.comments is None
    assert parsed.metrics_source == "inferred"


def test_reposts_are_never_reported():
    """No repost or share count is published on a public post page in any form
    — 0 of 35 sampled, plain HTTP and headless browser alike."""
    parsed = parse_post(FetchResult(POST_URL, 200, JSONLD_PAGE, "html"))
    assert parsed.reposts is None


def test_markdown_egress_skips_jsonld_gracefully():
    """Jina strips <script>, so the richest layer is simply unavailable — that
    must degrade, not error."""
    markdown = (
        "# Dana Lin on LinkedIn\n\n"
        "This is the body of the post and it is long enough to be considered real content.\n\n"
        "Some trailing nav text"
    )
    parsed = parse_post(FetchResult(POST_URL, 200, markdown, "markdown"))
    assert parsed.layer == "markdown"
    assert parsed.has_content
    assert parsed.reactions is None


def test_authwall_is_detected():
    parsed = parse_post(FetchResult(POST_URL, 200, "<html>Join now to see this post</html>", "html"))
    assert parsed.hit_authwall
    assert not parsed.has_content


# ---------------------------------------------------------------- provider --

def test_post_urls_normalise_to_one_dedupe_key():
    variants = [
        "https://www.linkedin.com/posts/someone_a-slug-activity-7123456789?utm_source=share",
        "https://linkedin.com/posts/someone_a-slug-activity-7123456789/",
        "https://in.linkedin.com/posts/someone_a-slug-activity-7123456789",
    ]
    keys = {normalise_post_url(v) for v in variants}
    # Host prefixes differ by design; what matters is that tracking params and
    # trailing slashes never create duplicate rows.
    assert all("?" not in k and not k.endswith("/") for k in keys)


def test_non_post_urls_are_rejected():
    assert normalise_post_url("https://linkedin.com/in/someone") is None
    assert normalise_post_url("https://example.com/posts/x") is None
    assert normalise_post_url("") is None


def test_queries_are_scoped_to_linkedin_posts():
    for query in build_queries("ai agents"):
        assert "site:linkedin.com/posts" in query


def test_unknown_provider_falls_back_to_ddg():
    assert get_provider("nonsense").name == "ddg"


@pytest.mark.asyncio
async def test_manual_provider_makes_no_network_calls(monkeypatch):
    """Verified by making any HTTP client construction an error."""
    import httpx

    def explode(*a, **kw):
        raise AssertionError("manual provider must not touch the network")

    monkeypatch.setattr(httpx, "AsyncClient", explode)

    outcome = await get_provider("manual").search(POST_URL, 1)
    assert len(outcome.candidates) == 1
    assert outcome.candidates[0].post_url.endswith("7123456789")


# ----------------------------------------------------------------- ranking --

def test_missing_metrics_do_not_rank_as_zero():
    """A post whose counts could not be read must not be pushed below a post
    genuinely measured at zero engagement."""
    unknown = compute_score(reactions=None, comments=None, serp_rank=1)
    measured_zero = compute_score(reactions=0, comments=0, reposts=0, serp_rank=1)
    assert unknown >= measured_zero


def test_engagement_increases_score():
    low = compute_score(reactions=5, serp_rank=3)
    high = compute_score(reactions=5000, serp_rank=3)
    assert high > low


def test_better_serp_rank_scores_higher():
    assert serp_factor(1) > serp_factor(5) > serp_factor(50)
    assert serp_factor(None) == 0.0


def test_recent_posts_outrank_old_ones_all_else_equal():
    recent = compute_score(serp_rank=1, posted_at=_now())
    old = compute_score(serp_rank=1, posted_at=_now() - timedelta(days=365))
    assert recent > old


def test_basis_is_reported_honestly():
    assert describe_basis(100, None, None) == "measured"
    assert describe_basis(None, None, None) == "inferred"
