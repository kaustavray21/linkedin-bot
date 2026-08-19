"""
app/services/research_service.py

Reads the web about a topic, so a draft can be written from findings rather than
from the model's recollection.

Separate from `discovery/` on purpose. That pipeline searches
`site:linkedin.com/posts` (`providers.py:121-123`) because it is hunting for
exemplar POSTS to clone the shape of. This is hunting for FACTS about a subject,
so it uses the same search library with no site filter and its own extraction —
`parse_post` understands LinkedIn markup and nothing else.

## What bounds it

The user is waiting on a generate, so the whole run is capped: a handful of
results, fetched concurrently, each page truncated before it reaches the model.
A run that cannot finish returns what it has rather than holding the draft.

Its own fetch budget, not discovery's. Sharing one would let a busy research day
spend the quota that finds exemplars, and the two are not worth the same.
The circuit breaker IS shared — being blocked belongs to this IP, not to a
subsystem.

## What it refuses to do

`AIService` answers with canned marketing copy when Gemini is unreachable
(`ai_service.py:54-56, 118-123`). As a research brief that copy would become
prompt context and steer the entire post while looking like findings, so
`is_template_fallback` drops it and the caller is told there are no notes. The
same reasoning as taxonomy guard 6.

Nothing here is silent. Empty search, dead pages and a refused summary all
return `ResearchNotes` with `ok = False` and a reason the caller can show. A
post that reads as researched but was not is the outcome to avoid.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field

from bs4 import BeautifulSoup

from app.core.config import settings
from app.core.logger import get_logger
from app.services.ai_service import AIService, is_template_fallback
from app.services.discovery.egress.base import EgressError
from app.services.discovery.egress.strategies import DirectEgress
from app.services.discovery.fetcher import fetcher

log = get_logger(tag="research")

# Stripped before the text is read: none of it is prose.
_DEAD_TAGS = ("script", "style", "nav", "header", "footer", "aside", "form", "noscript")


@dataclass
class Source:
    title: str
    url: str


@dataclass
class ResearchNotes:
    notes: str = ""
    sources: list[Source] = field(default_factory=list)
    ok: bool = False
    reason: str | None = None
    pages_read: int = 0

    @property
    def has_notes(self) -> bool:
        return self.ok and bool(self.notes.strip())


def _readable_text(html: str) -> str:
    """Prose from an arbitrary page. Best-effort by nature — this is not a
    reader-mode implementation, and pages that bury their content in scripts
    will yield little. Yielding little is fine; yielding navigation chrome as
    though it were content is not, which is why the dead tags go first."""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(list(_DEAD_TAGS)):
        tag.decompose()

    text = soup.get_text("\n")
    lines = [ln.strip() for ln in text.split("\n")]
    # One-word lines are almost always menu items rather than sentences.
    return "\n".join(ln for ln in lines if len(ln.split()) > 3)


async def _search(topic: str, limit: int) -> list[Source]:
    """General web search. No site filter — that is the whole difference from
    discovery, and the reason this does not reuse build_queries()."""
    try:
        from ddgs import DDGS
    except ImportError:
        log.warning("ddgs is not installed; research unavailable")
        return []

    def _run() -> list[Source]:
        found: list[Source] = []
        try:
            with DDGS() as ddgs:
                for row in ddgs.text(topic, max_results=limit):
                    url = row.get("href") or row.get("url")
                    if url:
                        found.append(Source(title=(row.get("title") or "").strip(), url=url))
        except Exception as exc:
            log.warning("research search failed", error=str(exc))
        return found

    return await asyncio.to_thread(_run)


async def _fetch_one(source: Source) -> tuple[Source, str] | None:
    """Runs concurrently. Touches the network and nothing else — no database,
    no shared state, the same rule the discovery fetcher follows."""
    try:
        result = await DirectEgress().fetch(source.url)
    except EgressError as exc:
        log.warning("research fetch failed", url=source.url, error=str(exc))
        return None
    if not result.ok:
        return None

    text = _readable_text(result.content)
    if len(text) < settings.research_min_page_chars:
        return None
    return source, text[: settings.research_page_char_cap]


def build_research_prompt(topic: str, pages: list[tuple[Source, str]]) -> str:
    extracts = "\n\n".join(
        f"[{i}] {src.title or src.url}\n{text}" for i, (src, text) in enumerate(pages, 1)
    )
    return f"""Read these web extracts and write research notes on: {topic}

{extracts}

Rules:
- Report only what the extracts actually say. Do not add knowledge of your own.
- Attach the bracketed source number to every claim, like [2].
- Prefer specifics — numbers, names, dates — over general statements.
- If the extracts do not address the topic, reply with exactly: NO FINDINGS
- 10 short bullet points at most, no preamble."""


async def research_topic(
    topic: str,
    ai_service: AIService | None = None,
) -> ResearchNotes:
    """Search, read and condense. Never raises — every failure is a reason."""
    topic = (topic or "").strip()
    if not topic:
        return ResearchNotes(reason="no topic to research")

    if fetcher.any_circuit_open():
        return ResearchNotes(reason="skipped research — egress is cooling down after blocks")

    sources = await _search(topic, settings.research_result_limit)
    if not sources:
        return ResearchNotes(reason="web search returned nothing for this topic")

    fetched = await asyncio.gather(*(_fetch_one(s) for s in sources))
    pages = [p for p in fetched if p is not None]
    if not pages:
        return ResearchNotes(reason="none of the search results could be read")

    ai = ai_service or AIService(provider="gemini")
    try:
        raw = await ai.generate_with_gemini(build_research_prompt(topic, pages))
    except Exception as exc:
        return ResearchNotes(reason=f"the summariser was unavailable: {exc}",
                             pages_read=len(pages))

    # Without this the canned template becomes prompt context and quietly steers
    # the whole post while looking like findings.
    if is_template_fallback(raw):
        log.warning("Research summariser returned placeholder copy; discarding")
        return ResearchNotes(reason="the summariser was unavailable (placeholder response)",
                             pages_read=len(pages))

    notes = (raw or "").strip()
    if not notes or re.search(r"^\s*NO FINDINGS\s*$", notes, re.IGNORECASE | re.MULTILINE):
        return ResearchNotes(reason="the sources did not address this topic",
                             pages_read=len(pages))

    return ResearchNotes(
        notes=notes,
        sources=[src for src, _ in pages],
        ok=True,
        pages_read=len(pages),
    )
