"""
app/services/discovery/egress/strategies.py

The three ways a fetch can leave this machine, plus the registry that picks one.

None of these send a cookie, a session token, or an Authorization header. That
is the property that keeps the user's LinkedIn account out of the blast radius:
an unauthenticated request can be rate-limited or blocked by IP, but there is no
account attached to it to restrict.
"""

from __future__ import annotations

import httpx

from app.core.config import settings
from app.core.logger import get_logger
from app.services.discovery.egress.base import EgressError, FetchResult

log = get_logger(tag="discovery")

# A real browser UA. Not an attempt at disguise — sending a Python default UA to
# a consumer site reliably gets a challenge page rather than content, which
# would just produce parse failures we would then misdiagnose.
BROWSER_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36"
)

COMMON_HEADERS = {
    "User-Agent": BROWSER_UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


class DirectEgress:
    """Straight to the origin. Returns raw HTML, so every parser layer works."""

    name = "direct"

    async def fetch(self, url: str) -> FetchResult:
        try:
            async with httpx.AsyncClient(
                timeout=settings.discovery_fetch_timeout,
                follow_redirects=True,
            ) as client:
                response = await client.get(url, headers=COMMON_HEADERS)
        except httpx.HTTPError as exc:
            raise EgressError(f"direct fetch failed: {exc}") from exc

        return FetchResult(
            url=url,
            status_code=response.status_code,
            content=response.text,
            content_kind="html",
            final_url=str(response.url),
            egress=self.name,
        )


class JinaEgress:
    """Via r.jina.ai, so the origin sees Jina's infrastructure rather than us.

    Returns rendered markdown. <script> blocks — and therefore the JSON-LD that
    carries the richest post data — do not survive that rendering, so this
    strategy yields strictly less than `direct`. It exists as the fallback for
    when direct fetches start getting blocked, not as an upgrade.
    """

    name = "jina"

    async def fetch(self, url: str) -> FetchResult:
        proxied = f"{settings.jina_reader_base.rstrip('/')}/{url}"
        try:
            async with httpx.AsyncClient(
                timeout=settings.discovery_fetch_timeout,
                follow_redirects=True,
            ) as client:
                response = await client.get(proxied, headers={"User-Agent": BROWSER_UA})
        except httpx.HTTPError as exc:
            raise EgressError(f"jina fetch failed: {exc}") from exc

        return FetchResult(
            url=url,
            status_code=response.status_code,
            content=response.text,
            content_kind="markdown",
            final_url=str(response.url),
            egress=self.name,
        )


class ProxyEgress:
    """Direct, but through a configured proxy. Raw HTML is preserved."""

    name = "proxy"

    async def fetch(self, url: str) -> FetchResult:
        if not settings.discovery_proxy_url:
            raise EgressError("DISCOVERY_PROXY_URL is not set")

        try:
            async with httpx.AsyncClient(
                timeout=settings.discovery_fetch_timeout,
                follow_redirects=True,
                proxy=settings.discovery_proxy_url,
            ) as client:
                response = await client.get(url, headers=COMMON_HEADERS)
        except httpx.HTTPError as exc:
            raise EgressError(f"proxy fetch failed: {exc}") from exc

        return FetchResult(
            url=url,
            status_code=response.status_code,
            content=response.text,
            content_kind="html",
            final_url=str(response.url),
            egress=self.name,
        )


_STRATEGIES = {
    DirectEgress.name: DirectEgress,
    JinaEgress.name: JinaEgress,
    ProxyEgress.name: ProxyEgress,
}


def get_egress(name: str | None = None):
    """Resolve a strategy by name, falling back to `direct`.

    An unrecognised value must never resolve to "no egress" or to a silently
    different network path — it falls back to the documented default and says so.
    """
    requested = (name or settings.discovery_egress or "direct").strip().lower()
    strategy = _STRATEGIES.get(requested)
    if strategy is None:
        log.warning(
            "Unknown egress strategy; falling back to direct",
            requested=requested,
            known=sorted(_STRATEGIES),
        )
        strategy = DirectEgress
    return strategy()


def available_strategies() -> list[str]:
    return sorted(_STRATEGIES)
