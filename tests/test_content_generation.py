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


# ------------------------------------------------------- paragraph control --

FIVE_BLOCK = """I shipped a product nobody wanted.

It took eight months.

The mistake was obvious afterwards: I never asked a single customer what they needed.

Now I speak to five people before writing any code at all.

That one habit changed how I build everything.

#BuildInPublic #Startups"""

FRESH_DRAFT = (
    "Alpha one two three. Beta four five six. Gamma seven eight nine. "
    "Delta ten eleven twelve. Epsilon thirteen fourteen.\n\n#Fresh #Tags"
)


@pytest.mark.asyncio
async def test_paragraph_count_overrides_the_exemplar_shape():
    """The plan's check: clone a 5-block exemplar asking for 3, get exactly 3,
    and the similarity gate still passes."""
    assert len(extract_skeleton(FIVE_BLOCK).content_blocks) == 5

    ai = AsyncMock()
    ai.generate_with_gemini.return_value = FRESH_DRAFT

    text, report = await generate_with_layout(
        topic="shipping", exemplar=FIVE_BLOCK, style=extract_style_profile([FIVE_BLOCK]),
        num_paragraphs=3, ai_service=ai,
    )

    prose = [b for b in text.split("\n\n") if not b.strip().startswith("#")]
    assert len(prose) == 3
    assert report.passed


@pytest.mark.asyncio
async def test_omitting_the_paragraph_count_keeps_the_exemplar_shape():
    """Cloning a post's structure is the default; the control is the exception."""
    ai = AsyncMock()
    ai.generate_with_gemini.return_value = FRESH_DRAFT

    text, _ = await generate_with_layout(
        topic="shipping", exemplar=FIVE_BLOCK, style=extract_style_profile([FIVE_BLOCK]),
        ai_service=ai,
    )

    prose = [b for b in text.split("\n\n") if not b.strip().startswith("#")]
    assert len(prose) == 5


@pytest.mark.asyncio
async def test_the_prompt_asks_for_the_retargeted_count_too():
    """Prompt and enforcement must agree. If the template still described five
    blocks, the model would be asked for one shape and corrected into another."""
    ai = AsyncMock()
    ai.generate_with_gemini.return_value = FRESH_DRAFT

    await generate_with_layout(
        topic="shipping", exemplar=FIVE_BLOCK, style=extract_style_profile([FIVE_BLOCK]),
        num_paragraphs=2, ai_service=ai,
    )

    prompt = ai.generate_with_gemini.await_args_list[0][0][0]
    assert "Block 3:" in prompt      # 2 prose blocks + the hashtag block
    assert "Block 4:" not in prompt
