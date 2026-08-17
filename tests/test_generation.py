"""Prompt-steering coverage for the hook-style and vocabulary overrides.

These assertions used to live behind POST /generate/styled-post, which was
removed with the reference subsystem. The behaviour they covered — that the two
UI overrides actually reach the prompt — is still live, so the tests moved down
to `build_prompt` rather than being deleted with the endpoint. Testing the
function directly is also where they belonged: the endpoint was never the thing
under test.
"""

from __future__ import annotations

from app.services.content_generation_service import build_prompt
from app.services.layout_service import extract_skeleton
from app.services.style_service import extract_style_profile

EXEMPLAR = (
    "I failed.\n\nTwice.\n\nHere is what it taught me about shipping.\n\n#BuildInPublic"
)


def _prompt(**overrides) -> str:
    return build_prompt(
        topic="writing clean Python",
        exemplar=EXEMPLAR,
        user_notes="",
        style=extract_style_profile([EXEMPLAR]),
        skeleton=extract_skeleton(EXEMPLAR),
        **overrides,
    )


def test_hook_style_override_reaches_the_prompt():
    assert "question" in _prompt(hook_style="question").lower()


def test_word_type_override_reaches_the_prompt():
    prompt = _prompt(word_type="simple_direct").lower()
    assert "simple, clear, and direct vocabulary" in prompt


def test_overrides_are_absent_when_not_requested():
    """Auto must mean auto — an unset override must not smuggle in a default."""
    baseline = _prompt().lower()
    assert "simple, clear, and direct vocabulary" not in baseline


def test_variation_index_changes_the_angle():
    """Variations must differ in instruction, not just in sampling luck."""
    first = _prompt(variation_index=0)
    second = _prompt(variation_index=1)
    third = _prompt(variation_index=2)

    assert first != second != third
    assert "lead with data" in second.lower()
    assert "lead with narrative" in third.lower()
