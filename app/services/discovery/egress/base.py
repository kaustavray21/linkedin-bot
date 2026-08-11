"""
app/services/discovery/egress/base.py

How a post-page fetch reaches the network.

This is a swappable strategy rather than a hardcoded httpx call for one reason:
the fetches are unauthenticated, but they still originate from somewhere. Going
out directly means they come from the same IP the user browses LinkedIn from.
That does not put their account at risk — no session or cookie is involved — but
it is a coupling worth being able to remove without touching code.

`direct` is the default because it returns raw HTML, and raw HTML is where the
JSON-LD block lives. Reader proxies return rendered markdown with <script> tags
stripped, which silently removes the richest parsing layer. content_kind exists
so the parser knows which layers are actually available instead of failing and
reporting an empty post.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass
class FetchResult:
    url: str
    status_code: int
    content: str
    content_kind: str          # "html" | "markdown"
    final_url: str = ""
    egress: str = ""

    @property
    def ok(self) -> bool:
        return 200 <= self.status_code < 300 and bool(self.content)

    @property
    def is_blocked(self) -> bool:
        """LinkedIn answers automated traffic with 999; 429 is a plain rate limit.

        Both mean "stop", and continuing through them is what escalates a soft
        throttle into a hard block.
        """
        return self.status_code in (429, 999) or self.status_code == 403


class EgressStrategy(Protocol):
    name: str

    async def fetch(self, url: str) -> FetchResult: ...


class EgressError(RuntimeError):
    """Transport-level failure — the strategy could not produce a response."""
