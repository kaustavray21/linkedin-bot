"""
app/services/discovery/providers.py

Finds candidate LinkedIn post URLs for a topic.

Discovery is split from fetching on purpose: this step never touches LinkedIn.
It asks a search engine which public posts exist, which is why it costs nothing
and carries no platform risk. Only the follow-up fetch (fetcher.py) contacts
linkedin.com, and only for URLs surfaced here.

Provider notes, as of 2026:
  - ddg      : keyless and free, via the `ddgs` package. Unofficial, so it
               rate-limits and occasionally breaks. Default because it works
               with zero setup.
  - searxng  : self-hosted metasearch, keyless, aggregates ~70 engines. More
               reliable than ddg but needs a container running.
  - manual   : no network at all. Not part of the automated flow — kept as an
               escape hatch for a specific post that search will not surface.

The paid providers evaluated during planning are absent by design: Proxycurl
shut down in July 2025, and Apify/Bright Data both cost money the user does not
want to spend. Adding one later means implementing this same Protocol.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from typing import Protocol

import httpx

from app.core.config import settings
from app.core.logger import get_logger

log = get_logger(tag="discovery")

# Matches a LinkedIn post/activity permalink in any of its published shapes.
POST_URL_RE = re.compile(
    r"https?://(?:[a-z]{2,3}\.)?linkedin\.com/(?:posts|feed/update)/[^\s\"'<>)]+",
    re.IGNORECASE,
)


@dataclass
class DiscoveredCandidate:
    post_url: str
    title: str = ""
    snippet: str = ""
    serp_rank: int = 0
    source: str = ""
    author_name: str | None = None


@dataclass
class SearchOutcome:
    candidates: list[DiscoveredCandidate] = field(default_factory=list)
    provider: str = ""
    queries_run: list[str] = field(default_factory=list)
    error: str | None = None


class DiscoveryProvider(Protocol):
    name: str

    async def search(self, keyword: str, limit: int) -> SearchOutcome: ...


def normalise_post_url(url: str) -> str | None:
    """Strip tracking parameters so the same post dedupes to one row.

    Search engines append their own query strings; without this, the same post
    arriving from two queries would insert twice and be ranked twice.
    """
    match = POST_URL_RE.search(url or "")
    if not match:
        return None
    cleaned = match.group(0).split("?")[0].rstrip("/")
    return cleaned or None


def build_queries(keyword: str) -> list[str]:
    """A few angles on the same topic — one phrasing finds one slice of results."""
    keyword = keyword.strip()
    return [
        f'site:linkedin.com/posts "{keyword}"',
        f"site:linkedin.com/posts {keyword}",
        f'site:linkedin.com/posts {keyword} "lessons"',
    ]


class DuckDuckGoProvider:
    name = "ddg"

    async def search(self, keyword: str, limit: int) -> SearchOutcome:
        outcome = SearchOutcome(provider=self.name)
        try:
            from ddgs import DDGS
        except ImportError:
            outcome.error = "The 'ddgs' package is not installed"
            return outcome

        seen: set[str] = set()

        def _run() -> list[DiscoveredCandidate]:
            found: list[DiscoveredCandidate] = []
            with DDGS() as ddgs:
                for query in build_queries(keyword):
                    outcome.queries_run.append(query)
                    try:
                        results = list(ddgs.text(query, max_results=limit))
                    except Exception as exc:  # the library raises many shapes
                        log.warning("ddg query failed", query=query, error=str(exc))
                        continue

                    for rank, row in enumerate(results, start=1):
                        url = normalise_post_url(row.get("href") or row.get("url") or "")
                        if not url or url in seen:
                            continue
                        seen.add(url)
                        found.append(
                            DiscoveredCandidate(
                                post_url=url,
                                title=row.get("title", ""),
                                snippet=row.get("body", "") or row.get("snippet", ""),
                                serp_rank=rank,
                                source=self.name,
                            )
                        )
                    if len(found) >= limit:
                        break
            return found[:limit]

        # ddgs is synchronous; keep it off the event loop.
        try:
            outcome.candidates = await asyncio.to_thread(_run)
        except Exception as exc:
            outcome.error = f"DuckDuckGo search failed: {exc}"
        return outcome


class SearxngProvider:
    name = "searxng"

    async def search(self, keyword: str, limit: int) -> SearchOutcome:
        outcome = SearchOutcome(provider=self.name)
        seen: set[str] = set()
        base = settings.searxng_url.rstrip("/")

        try:
            async with httpx.AsyncClient(timeout=20) as client:
                for query in build_queries(keyword):
                    outcome.queries_run.append(query)
                    try:
                        response = await client.get(
                            f"{base}/search",
                            params={"q": query, "format": "json", "categories": "general"},
                        )
                    except httpx.HTTPError as exc:
                        log.warning("SearXNG query failed", query=query, error=str(exc))
                        continue

                    if response.is_error:
                        # A SearXNG that has not enabled the JSON output format
                        # answers 403 here — a config problem, not a search miss.
                        outcome.error = (
                            f"SearXNG returned {response.status_code}. "
                            "Confirm 'json' is in its search.formats setting."
                        )
                        continue

                    for rank, row in enumerate(response.json().get("results", []), start=1):
                        url = normalise_post_url(row.get("url", ""))
                        if not url or url in seen:
                            continue
                        seen.add(url)
                        outcome.candidates.append(
                            DiscoveredCandidate(
                                post_url=url,
                                title=row.get("title", ""),
                                snippet=row.get("content", ""),
                                serp_rank=rank,
                                source=self.name,
                            )
                        )
                    if len(outcome.candidates) >= limit:
                        break
        except Exception as exc:
            outcome.error = f"SearXNG search failed: {exc}"

        outcome.candidates = outcome.candidates[:limit]
        return outcome


class ManualProvider:
    """Accepts a URL the user already has. Makes no network calls of any kind."""

    name = "manual"

    async def search(self, keyword: str, limit: int) -> SearchOutcome:
        outcome = SearchOutcome(provider=self.name)
        url = normalise_post_url(keyword)
        if url:
            outcome.candidates.append(
                DiscoveredCandidate(post_url=url, serp_rank=1, source=self.name)
            )
        else:
            outcome.error = "Manual provider needs a LinkedIn post URL"
        return outcome


_PROVIDERS = {
    DuckDuckGoProvider.name: DuckDuckGoProvider,
    SearxngProvider.name: SearxngProvider,
    ManualProvider.name: ManualProvider,
}


def get_provider(name: str | None = None) -> DiscoveryProvider:
    requested = (name or settings.discovery_provider or "ddg").strip().lower()
    provider = _PROVIDERS.get(requested)
    if provider is None:
        log.warning(
            "Unknown discovery provider; falling back to ddg",
            requested=requested,
            known=sorted(_PROVIDERS),
        )
        provider = DuckDuckGoProvider
    return provider()


def available_providers() -> list[str]:
    return sorted(_PROVIDERS)
