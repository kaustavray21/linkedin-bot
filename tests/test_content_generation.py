from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.services.content_generation_service import (
    build_prompt,
    generate_with_layout,
    select_representative,
)
from app.services.layout_service import extract_skeleton
from app.services.style_service import extract_style_profile

EXEMPLAR = """I failed twice.

Badly.

Here is the one thing both attempts had in common, and why it took me years to see it.

#BuildInPublic #Lessons"""


def _style():
    return extract_style_profile([EXEMPLAR])


@pytest.mark.asyncio
async def test_placeholder_content_is_rejected_not_returned():
    """AIService degrades to canned marketing copy when Gemini is unreachable.
    That copy is unrelated to the exemplar, so it sails through the similarity
    gate — it has to be caught explicitly or the user receives boilerplate
    believing it was written for them."""
    ai = AsyncMock()
    ai.generate_with_gemini.return_value = (
        "Excited to share my latest insights on FastAPI! Stay tuned for more updates."
    )

    with pytest.raises(ValueError, match="placeholder content"):
        await generate_with_layout(
            topic="FastAPI", exemplar=EXEMPLAR, style=_style(), ai_service=ai
        )


@pytest.mark.asyncio
async def test_near_copy_is_retried_then_refused():
    """Echoing the exemplar back must never reach the user."""
    ai = AsyncMock()
    ai.generate_with_gemini.return_value = EXEMPLAR

    with pytest.raises(ValueError, match="sufficiently original"):
        await generate_with_layout(
            topic="failure", exemplar=EXEMPLAR, style=_style(), ai_service=ai
        )

    # One initial attempt plus the configured retries.
    from app.core.config import settings
    assert ai.generate_with_gemini.await_count == settings.similarity_max_retries + 1


@pytest.mark.asyncio
async def test_retry_prompt_tells_the_model_what_went_wrong():
    ai = AsyncMock()
    ai.generate_with_gemini.side_effect = [
        EXEMPLAR,                                    # rejected
        "Totally fresh wording.\n\nShort.\n\nA closing thought entirely of my own making here.\n\n#New #Ideas",
    ]

    text, report = await generate_with_layout(
        topic="failure", exemplar=EXEMPLAR, style=_style(), ai_service=ai
    )

    assert report.passed
    second_prompt = ai.generate_with_gemini.await_args_list[1][0][0]
    assert "reused wording" in second_prompt
    assert text


@pytest.mark.asyncio
async def test_output_is_reshaped_to_the_exemplar_skeleton():
    """The model returns one dense blob; the enforcer must impose the shape."""
    ai = AsyncMock()
    ai.generate_with_gemini.return_value = (
        "Alpha happened. Beta occurred. Gamma delta epsilon zeta eta theta iota kappa.\n\n#New #Tags"
    )

    text, _ = await generate_with_layout(
        topic="x", exemplar=EXEMPLAR, style=_style(), ai_service=ai
    )

    expected = extract_skeleton(EXEMPLAR).total_blocks
    assert len(text.split("\n\n")) == expected


def test_prompt_carries_template_and_fenced_exemplar():
    skeleton = extract_skeleton(EXEMPLAR)
    prompt = build_prompt(
        topic="shipping",
        exemplar=EXEMPLAR,
        user_notes="keep it blunt",
        style=_style(),
        skeleton=skeleton,
    )

    assert "STRUCTURE_REFERENCE" in prompt
    assert "Block 1:" in prompt
    assert "keep it blunt" in prompt
    assert "do not reproduce that list verbatim" in prompt.lower()


def test_representative_is_a_real_post_not_an_average():
    """The blend must be an actual post's shape. A synthesised mean shape is
    precisely what made output generic."""
    posts = [
        "One.\n\nTwo.",
        "A.\n\nB.\n\nC.",
        "Single block of moderately long prose that runs on for a while without breaks.",
    ]
    chosen = select_representative(posts)
    assert chosen in posts


def test_representative_of_single_post_is_that_post():
    assert select_representative([EXEMPLAR]) == EXEMPLAR


def test_representative_rejects_empty_set():
    with pytest.raises(ValueError):
        select_representative([])
    with pytest.raises(ValueError):
        select_representative(["   ", ""])
