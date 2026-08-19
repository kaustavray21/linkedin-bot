from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.core.config import settings
from app.services.discovery.egress.base import EgressError, FetchResult
from app.services.research_service import (
    Source,
    _readable_text,
    build_research_prompt,
    research_topic,
)

PAGE = """
<html><head><title>t</title><style>.x{}</style></head><body>
<nav>Home About Contact</nav>
<p>Kubernetes adoption grew by 27 percent among mid sized engineering teams.</p>
<p>The main driver was cost control rather than any scaling requirement.</p>
<footer>Copyright</footer>
</body></html>
"""


@pytest.fixture(autouse=True)
def _bounds(monkeypatch):
    monkeypatch.setattr(settings, "research_result_limit", 3)
    monkeypatch.setattr(settings, "research_min_page_chars", 40)
    monkeypatch.setattr("app.services.research_service.fetcher.any_circuit_open", lambda: False)


def _search(sources):
    async def fake(topic, limit):
        return sources
    return fake


def _serve(page=PAGE, ok=True):
    async def fake(self, url):
        return FetchResult(url, 200 if ok else 500, page, "html")
    return fake


def _ai(reply):
    ai = AsyncMock()
    ai.generate_with_gemini.return_value = reply
    return ai


TWO = [Source("A", "https://a.invalid/1"), Source("B", "https://b.invalid/2")]


# ------------------------------------------------------------------ extraction --

def test_navigation_chrome_is_not_treated_as_content():
    text = _readable_text(PAGE)
    assert "Kubernetes adoption grew" in text
    assert "Home About Contact" not in text
    assert "Copyright" not in text


# --------------------------------------------------------------- the happy path --

@pytest.mark.asyncio
async def test_research_returns_notes_and_the_sources_behind_them(monkeypatch):
    monkeypatch.setattr("app.services.research_service._search", _search(TWO))
    monkeypatch.setattr("app.services.research_service.DirectEgress.fetch", _serve())

    notes = await research_topic("kubernetes", ai_service=_ai("- adoption up 27% [1]"))

    assert notes.ok and notes.has_notes
    assert "27%" in notes.notes
    # Sources travel with the notes so a claim can be checked before publishing.
    assert [s.url for s in notes.sources] == [s.url for s in TWO]
    assert notes.pages_read == 2


def test_the_prompt_forbids_inventing_and_demands_citations():
    prompt = build_research_prompt("kubernetes", [(TWO[0], "some extract")])
    assert "Do not add knowledge of your own" in prompt
    assert "bracketed source number" in prompt
    assert "NO FINDINGS" in prompt


# ------------------------------------------------------- every way it says no --

@pytest.mark.asyncio
async def test_placeholder_copy_never_becomes_research(monkeypatch):
    """The one that cannot be skipped. Canned copy accepted here becomes prompt
    context and steers the whole post while looking like findings."""
    monkeypatch.setattr("app.services.research_service._search", _search(TWO))
    monkeypatch.setattr("app.services.research_service.DirectEgress.fetch", _serve())

    notes = await research_topic(
        "kubernetes",
        ai_service=_ai("Excited to share my latest insights on kubernetes! Stay tuned for more updates."),
    )

    assert not notes.ok
    assert "placeholder" in notes.reason
    assert notes.notes == ""


@pytest.mark.asyncio
async def test_a_summariser_outage_yields_no_notes_rather_than_guesses(monkeypatch):
    monkeypatch.setattr("app.services.research_service._search", _search(TWO))
    monkeypatch.setattr("app.services.research_service.DirectEgress.fetch", _serve())
    ai = AsyncMock()
    ai.generate_with_gemini.side_effect = RuntimeError("503")

    notes = await research_topic("kubernetes", ai_service=ai)
    assert not notes.ok and "unavailable" in notes.reason


@pytest.mark.asyncio
async def test_sources_that_do_not_address_the_topic_are_reported_as_such(monkeypatch):
    monkeypatch.setattr("app.services.research_service._search", _search(TWO))
    monkeypatch.setattr("app.services.research_service.DirectEgress.fetch", _serve())

    notes = await research_topic("kubernetes", ai_service=_ai("NO FINDINGS"))
    assert not notes.ok
    assert "did not address" in notes.reason


@pytest.mark.asyncio
async def test_an_empty_search_is_a_reason_not_an_error(monkeypatch):
    monkeypatch.setattr("app.services.research_service._search", _search([]))
    notes = await research_topic("kubernetes", ai_service=_ai("unused"))
    assert not notes.ok and "returned nothing" in notes.reason


@pytest.mark.asyncio
async def test_unreadable_pages_are_a_reason_not_an_error(monkeypatch):
    monkeypatch.setattr("app.services.research_service._search", _search(TWO))

    async def dead(self, url):
        raise EgressError("blocked")

    monkeypatch.setattr("app.services.research_service.DirectEgress.fetch", dead)
    notes = await research_topic("kubernetes", ai_service=_ai("unused"))
    assert not notes.ok and "none of the search results could be read" == notes.reason


@pytest.mark.asyncio
async def test_an_open_circuit_stops_research_before_it_starts(monkeypatch):
    """Being blocked belongs to this IP, not to one subsystem."""
    monkeypatch.setattr("app.services.research_service.fetcher.any_circuit_open", lambda: True)
    called = AsyncMock()
    monkeypatch.setattr("app.services.research_service._search", called)

    notes = await research_topic("kubernetes")
    assert not notes.ok and "cooling down" in notes.reason
    called.assert_not_awaited()


@pytest.mark.asyncio
async def test_an_empty_topic_researches_nothing(monkeypatch):
    notes = await research_topic("   ")
    assert not notes.ok and "no topic" in notes.reason


@pytest.mark.asyncio
async def test_pages_are_truncated_before_reaching_the_model(monkeypatch):
    monkeypatch.setattr(settings, "research_page_char_cap", 100)
    monkeypatch.setattr("app.services.research_service._search", _search([TWO[0]]))
    long_page = "<html><body><p>" + ("a sentence with several words here. " * 500) + "</p></body></html>"
    monkeypatch.setattr("app.services.research_service.DirectEgress.fetch", _serve(long_page))

    ai = _ai("- something [1]")
    await research_topic("kubernetes", ai_service=ai)

    prompt = ai.generate_with_gemini.await_args_list[0][0][0]
    # The user is waiting on a draft; an unbounded page would blow the budget.
    assert len(prompt) < 1000
