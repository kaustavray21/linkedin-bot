"""Concurrency guarantees of the parallel discovery fetcher.

These are the tests that would otherwise have been written after the first
production failure. Every one of them covers a case that passes a smoke test and
breaks under load:

  - the writer must be the only coroutine touching the session
  - the daily cap must hold when N workers check it simultaneously
  - a tripped breaker must halt the queued wave, not just the next request
  - an early stop must bound how much budget the wave already spent
  - a duplicate URL must not abort the run
  - the rate ceiling must actually cap requests per second

The network is replaced at `service.strategy_fetch` / `fetcher.strategy_fetch`,
so nothing here reaches linkedin.com.
"""

from __future__ import annotations

import asyncio
import time

import pytest
from sqlalchemy import func, select

from app.core.config import settings
from app.database.models import DiscoveredPost
from app.services.discovery import fetcher as fetcher_module
from app.services.discovery.egress.base import FetchResult
from app.services.discovery.fetcher import RateLimitedFetcher
from app.services.discovery.providers import DiscoveredCandidate, SearchOutcome
from app.services.discovery.service import run_discovery

PAGE = (
    '<html><head><script type="application/ld+json">'
    '{"@type":"DiscussionForumPosting","articleBody":"Line one.\\n\\nLine two.",'
    '"author":{"name":"A Person"}}'
    "</script></head><body>ok</body></html>"
)


class FakeNetwork:
    """Stands in for the egress layer. Records arrival times per request."""

    def __init__(self, latency: float = 0.02, block_after: int | None = None) -> None:
        self.latency = latency
        self.block_after = block_after
        self.arrivals: list[float] = []

    async def __call__(self, name: str, url: str) -> FetchResult:
        self.arrivals.append(time.monotonic())
        n = len(self.arrivals)
        await asyncio.sleep(self.latency)
        blocked = self.block_after is not None and n > self.block_after
        return FetchResult(
            url=url,
            status_code=999 if blocked else 200,
            content="" if blocked else PAGE,
            content_kind="html",
            egress=name,
        )

    @property
    def hits(self) -> int:
        return len(self.arrivals)

    @property
    def observed_rate(self) -> float:
        if len(self.arrivals) < 2:
            return 0.0
        span = self.arrivals[-1] - self.arrivals[0]
        return (len(self.arrivals) - 1) / span if span > 0 else float("inf")


@pytest.fixture
def net(monkeypatch):
    """Swap the network and give each test a fresh fetcher (budget + breaker)."""

    def _install(**kwargs) -> FakeNetwork:
        network = FakeNetwork(**kwargs)
        monkeypatch.setattr(fetcher_module, "strategy_fetch", network)
        fresh = RateLimitedFetcher()
        monkeypatch.setattr(fetcher_module, "fetcher", fresh)
        monkeypatch.setattr(
            "app.services.discovery.service.fetcher", fresh, raising=True
        )
        return network

    return _install


def _candidates(n: int, prefix: str = "p") -> list[DiscoveredCandidate]:
    return [
        DiscoveredCandidate(
            post_url=f"https://www.linkedin.com/posts/{prefix}{i}",
            snippet="snippet",
            serp_rank=i + 1,
            source="ddg",
        )
        for i in range(n)
    ]


def _provider(candidates, monkeypatch):
    class _P:
        name = "ddg"

        async def search(self, keyword: str, limit: int, **_options) -> SearchOutcome:
            return SearchOutcome(candidates=list(candidates), provider="ddg")

    monkeypatch.setattr(
        "app.services.discovery.service.get_provider", lambda *_a, **_k: _P()
    )


async def _stored(db) -> int:
    return (await db.execute(select(func.count(DiscoveredPost.id)))).scalar_one()


@pytest.mark.asyncio
async def test_parallel_run_persists_every_post(db_session, monkeypatch, net):
    """The bug this whole restructure exists for.

    Sharing one AsyncSession across concurrent tasks raises "Session is already
    flushing". Fetching parallelises; persistence must not.
    """
    network = net(latency=0.02)
    _provider(_candidates(12), monkeypatch)

    job = await run_discovery(db=db_session, keyword="topic", limit=12)

    assert job.status == "success"
    assert await _stored(db_session) == 12
    assert network.hits == 12


@pytest.mark.asyncio
async def test_daily_cap_holds_under_concurrency(db_session, monkeypatch, net):
    """N workers checking a cap of 5 must not all see the last unit."""
    network = net(latency=0.05)
    monkeypatch.setattr(settings, "discovery_daily_fetch_cap", 5)
    monkeypatch.setattr(settings, "discovery_requests_per_second", 50.0)
    _provider(_candidates(20), monkeypatch)

    await run_discovery(db=db_session, keyword="topic", limit=20)

    assert network.hits == 5, f"cap overrun: {network.hits} requests made"
    assert await _stored(db_session) == 5


@pytest.mark.asyncio
async def test_block_halts_the_wave(db_session, monkeypatch, net):
    """Blocking from request 3 must not still send all 30."""
    network = net(latency=0.02, block_after=3)
    monkeypatch.setattr(settings, "discovery_requests_per_second", 50.0)
    monkeypatch.setattr(settings, "discovery_circuit_threshold", 3)
    _provider(_candidates(30), monkeypatch)

    job = await run_discovery(db=db_session, keyword="topic", limit=30)

    assert network.hits < 30, "the tripped breaker did not stop queued work"
    assert network.hits <= 12, f"halted too late: {network.hits} requests"
    assert job.status == "partial"
    assert job.error


@pytest.mark.asyncio
async def test_stop_after_usable_overshoot_is_bounded(db_session, monkeypatch, net):
    """An early stop must not have already spent the whole wave."""
    network = net(latency=0.01)
    monkeypatch.setattr(settings, "discovery_requests_per_second", 50.0)
    monkeypatch.setattr(settings, "discovery_concurrency_max", 4)
    _provider(_candidates(30), monkeypatch)

    await run_discovery(
        db=db_session, keyword="topic", limit=30, stop_after_usable=1
    )

    assert network.hits <= 4, f"overshot by the wave, not the worker count: {network.hits}"
    assert network.hits >= 1


@pytest.mark.asyncio
async def test_duplicate_url_does_not_abort_the_run(db_session, monkeypatch, net):
    """A collision on the unique post_url must cost one row, not the run."""
    network = net(latency=0.01)
    monkeypatch.setattr(settings, "discovery_requests_per_second", 50.0)

    dupes = _candidates(6) + _candidates(3)          # 3 deliberate collisions
    _provider(dupes, monkeypatch)

    job = await run_discovery(db=db_session, keyword="topic", limit=9)

    assert await _stored(db_session) == 6, "duplicates were not absorbed"
    assert job.status == "success"
    assert network.hits == 9


@pytest.mark.asyncio
async def test_rate_ceiling_is_respected(db_session, monkeypatch, net):
    """The token bucket must cap requests/second, burst included."""
    network = net(latency=0.01)
    monkeypatch.setattr(settings, "discovery_requests_per_second", 20.0)
    monkeypatch.setattr(settings, "discovery_token_burst", 1.0)
    _provider(_candidates(20), monkeypatch)

    await run_discovery(db=db_session, keyword="topic", limit=20)

    assert network.observed_rate <= 20.0 * 1.5, (
        f"observed {network.observed_rate:.1f}/s against a 20/s cap — "
        "a full starting bucket lets the pool fire at once"
    )


@pytest.mark.asyncio
async def test_parallel_is_actually_faster_than_serial(db_session, monkeypatch, net):
    """Guards against a future change quietly re-serialising the fetch path."""
    network = net(latency=0.10)
    monkeypatch.setattr(settings, "discovery_requests_per_second", 50.0)
    monkeypatch.setattr(settings, "discovery_concurrency", 4)
    monkeypatch.setattr(settings, "discovery_concurrency_max", 4)
    _provider(_candidates(12), monkeypatch)

    started = time.monotonic()
    await run_discovery(db=db_session, keyword="topic", limit=12)
    elapsed = time.monotonic() - started

    serial = 12 * 0.10
    assert elapsed < serial * 0.6, (
        f"{elapsed:.2f}s for 12 fetches at 0.10s latency — serial would be {serial:.2f}s"
    )
