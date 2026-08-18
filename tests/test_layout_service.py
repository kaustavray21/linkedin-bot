from __future__ import annotations

import pytest

from app.services.layout_service import (
    LayoutSkeleton,
    enforce_layout,
    extract_skeleton,
    render_template,
    retarget_skeleton,
)

PUNCHY = """I failed.

Twice.

Here is what it taught me about shipping software that people actually use.

#BuildInPublic #Startups #Founders"""

FLOWING = """When I started building software professionally I assumed the hardest part would be the code itself, but experience has taught me otherwise.

The real difficulty is deciding what deserves to be built at all, and no framework will make that judgement for you."""


def test_single_word_paragraph_survives_as_its_own_block():
    """The whole point of the rewrite: a deliberate one-word paragraph must not
    be averaged away into a mid-length one."""
    skeleton = extract_skeleton(PUNCHY)

    assert skeleton.total_blocks == 4
    second = skeleton.blocks[1]
    assert len(second.lines) == 1
    assert second.lines[0].words == 1          # "Twice."
    assert second.lines[0].ends_with == "."


def test_hook_and_hashtag_placement_detected():
    skeleton = extract_skeleton(PUNCHY)
    assert skeleton.hook_lines == 1
    assert skeleton.hashtag_placement == "trailing_block"
    assert skeleton.hashtag_count == 3
    assert skeleton.blocks[-1].is_hashtag_block


def test_trailing_tag_on_prose_line_is_not_a_hashtag_block():
    """A line ending in one tag is still prose. Misreading it as the hashtag
    block would make enforce_layout hold back real content."""
    skeleton = extract_skeleton("We shipped it today #proud\n\nAnd it worked.")
    assert skeleton.hashtag_placement == "inline"
    assert not skeleton.blocks[0].is_hashtag_block


def test_flowing_and_punchy_skeletons_are_distinguishable():
    """These two shapes are what averaging collapses together."""
    punchy = extract_skeleton(PUNCHY)
    flowing = extract_skeleton(FLOWING)

    punchy_first_block_words = punchy.blocks[0].word_total
    flowing_first_block_words = flowing.blocks[0].word_total
    assert punchy_first_block_words < 5
    assert flowing_first_block_words > 20


def test_double_blank_line_rhythm_preserved():
    skeleton = extract_skeleton("One.\n\n\nTwo.")
    assert skeleton.blocks[0].blank_after == 2


def test_render_template_names_every_block():
    template = render_template(extract_skeleton(PUNCHY))
    assert "Block 1:" in template
    assert "Block 4:" in template
    assert "hashtags only" in template
    assert "1 word" in template


def test_empty_text_rejected():
    with pytest.raises(ValueError):
        extract_skeleton("   \n  ")


def test_round_trip_through_dict():
    """Skeletons persist to a JSON column and must survive the trip intact."""
    original = extract_skeleton(PUNCHY)
    restored = LayoutSkeleton.from_dict(original.to_dict())

    assert restored.total_blocks == original.total_blocks
    assert restored.hashtag_placement == original.hashtag_placement
    assert restored.blocks[1].lines[0].words == original.blocks[1].lines[0].words


# ------------------------------------------------------------------ enforcer --

def test_enforce_splits_a_single_blob_into_the_target_shape():
    skeleton = extract_skeleton(PUNCHY)
    blob = (
        "I quit my job. It happened twice. Here is what that taught me about "
        "building things people want.\n\n#Growth #Career #Lessons"
    )
    out = enforce_layout(blob, skeleton)
    blocks = out.split("\n\n")

    assert len(blocks) == skeleton.total_blocks
    assert blocks[-1].startswith("#")


def test_enforce_merges_excess_blocks():
    skeleton = extract_skeleton("One line.\n\nTwo line.")
    verbose = "Alpha.\n\nBeta.\n\nGamma.\n\nDelta."
    out = enforce_layout(verbose, skeleton)
    assert len(out.split("\n\n")) == 2


def test_enforce_is_idempotent():
    """Running the enforcer twice must not keep reshaping the text."""
    skeleton = extract_skeleton(PUNCHY)
    draft = "I left. It was hard. I learned a great deal from the whole experience.\n\n#One #Two #Three"

    once = enforce_layout(draft, skeleton)
    twice = enforce_layout(once, skeleton)
    assert once == twice


def test_enforce_preserves_double_blank_gap():
    skeleton = extract_skeleton("First.\n\n\nSecond.")
    out = enforce_layout("Alpha.\n\nBeta.", skeleton)
    assert "\n\n\n" in out


def test_enforce_never_invents_or_drops_words():
    """Regrouping only — the enforcer must not rewrite content."""
    skeleton = extract_skeleton(PUNCHY)
    draft = "Alpha beta. Gamma delta. Epsilon zeta eta theta.\n\n#One #Two #Three"

    before = sorted(draft.replace("\n", " ").split())
    after = sorted(enforce_layout(draft, skeleton).replace("\n", " ").split())
    assert before == after


def test_enforce_handles_empty_input():
    skeleton = extract_skeleton(PUNCHY)
    assert enforce_layout("", skeleton) == ""


# ------------------------------------------------------------- retargeting --

def test_retarget_shrinks_to_the_requested_paragraph_count():
    """PUNCHY has three prose blocks plus a hashtag block."""
    skeleton = extract_skeleton(PUNCHY)
    assert len(skeleton.content_blocks) == 3

    retargeted = retarget_skeleton(skeleton, 2)
    assert len(retargeted.content_blocks) == 2


def test_retarget_grows_to_the_requested_paragraph_count():
    skeleton = extract_skeleton(FLOWING)
    assert len(skeleton.content_blocks) == 2

    retargeted = retarget_skeleton(skeleton, 4)
    assert len(retargeted.content_blocks) == 4


def test_retarget_leaves_the_hashtag_block_alone():
    """Hashtags are not a paragraph — asking for two paragraphs must not spend
    one of them on the tag line, or consume the tag line to reach the count."""
    skeleton = extract_skeleton(PUNCHY)

    for target in (1, 2, 5):
        retargeted = retarget_skeleton(skeleton, target)
        assert len(retargeted.content_blocks) == target
        assert retargeted.blocks[-1].is_hashtag_block
        assert retargeted.hashtag_count == 3
        assert retargeted.hashtag_placement == "trailing_block"


def test_retarget_preserves_the_total_word_budget_when_shrinking():
    """Merging regroups lines; it must not silently change how long the post is."""
    skeleton = extract_skeleton(FLOWING)
    retargeted = retarget_skeleton(skeleton, 1)
    assert retargeted.total_words == skeleton.total_words


def test_retarget_can_grow_past_the_available_lines():
    """Every block is a single line here, so growing has no line boundary to
    split on and has to divide a line's own budget instead."""
    skeleton = extract_skeleton("One line only.")
    assert len(skeleton.content_blocks) == 1

    retargeted = retarget_skeleton(skeleton, 3)
    assert len(retargeted.content_blocks) == 3
    assert all(b.word_total >= 1 for b in retargeted.content_blocks)


def test_retarget_is_a_no_op_when_the_count_already_matches():
    skeleton = extract_skeleton(PUNCHY)
    assert retarget_skeleton(skeleton, 3) is skeleton


def test_retarget_rejects_a_zero_paragraph_request():
    skeleton = extract_skeleton(PUNCHY)
    with pytest.raises(ValueError):
        retarget_skeleton(skeleton, 0)


def test_retarget_does_not_mutate_the_original():
    skeleton = extract_skeleton(FLOWING)
    before = skeleton.to_dict()

    retarget_skeleton(skeleton, 5)
    assert skeleton.to_dict() == before


def test_retargeted_skeleton_drives_enforcement_to_the_same_count():
    """The reason this function exists: the template, the prompt and the
    enforcer must agree on the number the user asked for."""
    skeleton = retarget_skeleton(extract_skeleton(PUNCHY), 2)
    draft = "Alpha beta gamma. Delta epsilon. Zeta eta theta. Iota kappa.\n\n#One #Two #Three"

    enforced = enforce_layout(draft, skeleton)
    prose = [b for b in enforced.split("\n\n") if not b.strip().startswith("#")]
    assert len(prose) == 2


def test_retargeted_skeleton_still_renders_a_template():
    skeleton = retarget_skeleton(extract_skeleton(FLOWING), 4)
    rendered = render_template(skeleton)
    assert "Block 4:" in rendered
    assert "Block 5:" not in rendered
