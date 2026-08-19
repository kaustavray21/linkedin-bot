from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from app.database.models import PostType
from app.services.post_type_service import (
    build_classification_prompt,
    load_taxonomy,
    normalise_slug,
    proposal_similarity,
    propose_type,
    resolve_proposal,
    stale_types,
)

STORY_POST = """I shipped a product nobody wanted.

It took eight months to admit that.

Now I talk to five customers before writing a line of code."""


def _ai(reply: str) -> AsyncMock:
    ai = AsyncMock()
    ai.generate_with_gemini.return_value = reply
    return ai


async def _seed(db, *specs) -> None:
    for slug, label, description, origin in specs:
        db.add(PostType(slug=slug, label=label, description=description, origin=origin))
    await db.flush()


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ------------------------------------------------- guard 1: slug normalisation --

@pytest.mark.parametrize(
    "raw, expected",
    [
        ("Personal Story", "personal_story"),
        ("personal_story", "personal_story"),
        ("  Case   Study  ", "case_study"),
        ("listicles", "listicle"),
        ("Stories", "story"),
        ("announcements", "announcement"),
        ("Hot-Take!", "hot_take"),
        ("", ""),
    ],
)
def test_slug_normalisation_folds_surface_variants(raw, expected):
    assert normalise_slug(raw) == expected


def test_normalisation_does_not_mangle_words_ending_in_s():
    """`process` is not a plural. Stripping the trailing s would invent a type
    named `proces` that never matches anything again."""
    assert normalise_slug("process") == "process"


# ------------------------------------------------------ guard 2: near-dup snap --

def test_a_narrower_name_scores_as_the_same_type():
    """personal_story is a narrower name for story, not a new idea."""
    score = proposal_similarity(
        "personal_story", "Personal Story", "A story from the author's own life",
        "story", "Story", "A personal narrative with a turn",
    )
    assert score >= 0.6


def test_a_paraphrased_description_scores_as_the_same_type():
    score = proposal_similarity(
        "hot_take", "Hot take", "Argues against a position the audience holds",
        "contrarian", "Contrarian take", "Argues against a position the audience holds",
    )
    assert score >= 0.6


def test_a_genuinely_different_type_scores_low():
    score = proposal_similarity(
        "job_change", "Job change", "Announces the author is moving to a new employer",
        "listicle", "List", "Enumerated advice structured as a countable set",
    )
    assert score < 0.6


@pytest.mark.asyncio
async def test_near_duplicate_folds_into_the_existing_type(db_session):
    await _seed(db_session, ("story", "Story", "A personal narrative with a turn", "seed"))

    proposal = await propose_type(
        STORY_POST,
        await load_taxonomy(db_session),
        ai_service=_ai(
            '{"slug": "personal_story", "label": "Personal Story",'
            ' "description": "A personal narrative with a turn",'
            ' "why_new": "The existing story type is about other people, not the author themselves."}'
        ),
    )
    resolution = await resolve_proposal(db_session, proposal)

    assert resolution.slug == "story"
    assert resolution.snapped_to == "story"
    assert not resolution.created

    types = (await db_session.execute(select(PostType))).scalars().all()
    assert len(types) == 1


# ------------------------------------------------ guard 3: justification needed --

@pytest.mark.asyncio
async def test_a_new_type_without_a_reason_is_refused(db_session):
    proposal = await propose_type(
        STORY_POST,
        await load_taxonomy(db_session),
        ai_service=_ai('{"slug": "vibes", "label": "Vibes", "description": "Posts with vibes"}'),
    )
    assert proposal.refused
    assert "why" in proposal.refused

    resolution = await resolve_proposal(db_session, proposal)
    assert resolution.refused
    assert (await db_session.execute(select(PostType))).scalars().all() == []


@pytest.mark.asyncio
async def test_a_token_justification_is_not_enough(db_session):
    proposal = await propose_type(
        STORY_POST,
        await load_taxonomy(db_session),
        ai_service=_ai(
            '{"slug": "vibes", "label": "Vibes", "description": "Posts with vibes", "why_new": "different"}'
        ),
    )
    assert proposal.refused


# ---------------------------------------------------------- guard 4: growth brake --

@pytest.mark.asyncio
async def test_the_growth_brake_makes_new_types_harder_past_the_threshold(db_session, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "post_type_growth_brake", 3)
    monkeypatch.setattr(settings, "post_type_snap_threshold", 0.9)
    monkeypatch.setattr(settings, "post_type_brake_snap_threshold", 0.3)

    await _seed(
        db_session,
        ("story", "Story", "A personal narrative with a turn", "seed"),
        ("listicle", "List", "Enumerated advice", "seed"),
        ("question", "Question", "Opens with a question to the reader", "seed"),
    )

    proposal = await propose_type(
        STORY_POST,
        await load_taxonomy(db_session),
        ai_service=_ai(
            '{"slug": "narrative_turn", "label": "Narrative turn",'
            ' "description": "A personal narrative with a turn in it",'
            ' "why_new": "None of the listed types capture a narrative that pivots midway."}'
        ),
    )
    resolution = await resolve_proposal(db_session, proposal)

    # Above the brake the same proposal folds in rather than becoming a new row.
    assert not resolution.created
    assert resolution.snapped_to == "story"
    assert any("novelty bar raised" in n for n in resolution.notes)


# ------------------------------------------------------------ guard 5: decay --

@pytest.mark.asyncio
async def test_unused_ai_types_go_stale_but_seeds_never_do(db_session):
    old = _now() - timedelta(days=200)
    await _seed(
        db_session,
        ("story", "Story", "A personal narrative", "seed"),
        ("one_off", "One off", "Something coined once", "ai"),
        ("recent", "Recent", "Coined and used lately", "ai"),
    )
    for slug, used in (("story", old), ("one_off", old), ("recent", _now())):
        row = (
            await db_session.execute(select(PostType).where(PostType.slug == slug))
        ).scalar_one()
        row.last_used_at = used
    await db_session.flush()

    stale = await stale_types(db_session, days=90)
    slugs = {t.slug for t in stale}

    assert slugs == {"one_off"}


# ------------------------------------------------- guard 6: fallback detection --

@pytest.mark.asyncio
async def test_placeholder_copy_never_becomes_a_type(db_session):
    """The plan's stated check. AIService returns canned marketing copy when
    Gemini is unreachable; with auto-registration and nobody in the loop, that
    text would become a permanent type — and then appear in every later prompt."""
    proposal = await propose_type(
        STORY_POST,
        await load_taxonomy(db_session),
        ai_service=_ai(
            "Excited to share my latest insights on shipping! Stay tuned for more updates."
        ),
    )
    assert proposal.refused
    assert "unavailable" in proposal.refused

    resolution = await resolve_proposal(db_session, proposal)
    assert resolution.refused
    assert (await db_session.execute(select(PostType))).scalars().all() == []


@pytest.mark.asyncio
async def test_a_classifier_outage_refuses_rather_than_guessing(db_session):
    ai = AsyncMock()
    ai.generate_with_gemini.side_effect = RuntimeError("503 model overloaded")

    proposal = await propose_type(STORY_POST, await load_taxonomy(db_session), ai_service=ai)
    assert proposal.refused

    resolution = await resolve_proposal(db_session, proposal)
    assert resolution.refused
    assert (await db_session.execute(select(PostType))).scalars().all() == []


# ------------------------------------------------------------- the happy paths --

@pytest.mark.asyncio
async def test_an_existing_type_is_chosen_and_its_usage_recorded(db_session):
    await _seed(db_session, ("story", "Story", "A personal narrative with a turn", "seed"))

    proposal = await propose_type(
        STORY_POST, await load_taxonomy(db_session),
        ai_service=_ai('{"existing_slug": "story"}'),
    )
    resolution = await resolve_proposal(db_session, proposal)

    assert resolution.slug == "story"
    assert not resolution.created

    row = (await db_session.execute(select(PostType).where(PostType.slug == "story"))).scalar_one()
    assert row.usage_count == 1
    assert row.last_used_at is not None


@pytest.mark.asyncio
async def test_a_genuinely_new_type_registers_itself_with_its_reason(db_session):
    await _seed(db_session, ("story", "Story", "A personal narrative with a turn", "seed"))

    proposal = await propose_type(
        STORY_POST, await load_taxonomy(db_session),
        ai_service=_ai(
            '{"slug": "teardown", "label": "Teardown",'
            ' "description": "Dissects someone else\'s public work piece by piece",'
            ' "why_new": "Story covers the author\'s own experience; this analyses an external artefact instead."}'
        ),
    )
    resolution = await resolve_proposal(db_session, proposal)

    assert resolution.created
    assert resolution.slug == "teardown"

    row = (await db_session.execute(select(PostType).where(PostType.slug == "teardown"))).scalar_one()
    assert row.origin == "ai"
    assert "external artefact" in row.why_new
    assert row.usage_count == 1


@pytest.mark.asyncio
async def test_an_invented_existing_slug_is_refused_not_created(db_session):
    """Naming a type that is not in the list bypasses the justification guard —
    it has to be treated as a miss, not quietly turned into a new row."""
    await _seed(db_session, ("story", "Story", "A personal narrative", "seed"))

    proposal = await propose_type(
        STORY_POST, await load_taxonomy(db_session),
        ai_service=_ai('{"existing_slug": "thought_leadership"}'),
    )
    resolution = await resolve_proposal(db_session, proposal)

    assert resolution.refused
    assert (await db_session.execute(select(PostType))).scalars().all() != []
    assert len((await db_session.execute(select(PostType))).scalars().all()) == 1


@pytest.mark.asyncio
async def test_a_merged_type_resolves_to_its_survivor(db_session):
    await _seed(
        db_session,
        ("story", "Story", "A personal narrative", "seed"),
        ("personal_story", "Personal Story", "A narrative", "ai"),
    )
    survivor = (
        await db_session.execute(select(PostType).where(PostType.slug == "story"))
    ).scalar_one()
    loser = (
        await db_session.execute(select(PostType).where(PostType.slug == "personal_story"))
    ).scalar_one()
    loser.active = False
    loser.merged_into_id = survivor.id
    await db_session.flush()

    proposal = await propose_type(
        STORY_POST, await load_taxonomy(db_session),
        ai_service=_ai('{"existing_slug": "personal_story"}'),
    )
    resolution = await resolve_proposal(db_session, proposal)

    assert resolution.slug == "story"
    assert "merged into" in " ".join(resolution.notes)


@pytest.mark.asyncio
async def test_fenced_json_is_tolerated(db_session):
    """Models fence JSON despite being told not to; that is not a real failure."""
    await _seed(db_session, ("story", "Story", "A personal narrative", "seed"))

    proposal = await propose_type(
        STORY_POST, await load_taxonomy(db_session),
        ai_service=_ai('```json\n{"existing_slug": "story"}\n```'),
    )
    assert proposal.existing_slug == "story"


@pytest.mark.asyncio
async def test_unparseable_output_refuses(db_session):
    proposal = await propose_type(
        STORY_POST, await load_taxonomy(db_session),
        ai_service=_ai("I think this is a story, personally!"),
    )
    assert proposal.refused


@pytest.mark.asyncio
async def test_an_empty_post_is_not_classified(db_session):
    proposal = await propose_type("   ", await load_taxonomy(db_session))
    assert proposal.refused


@pytest.mark.asyncio
async def test_taxonomy_crosses_to_workers_as_plain_data(db_session):
    """Discovery classifies a wave of posts concurrently. ORM instances handed to
    another task lazy-load against a session that task does not own."""
    await _seed(db_session, ("story", "Story", "A personal narrative", "seed"))
    taxonomy = await load_taxonomy(db_session)

    assert all(isinstance(entry, dict) for entry in taxonomy)
    assert taxonomy[0]["slug"] == "story"


def test_the_prompt_lists_every_existing_type():
    prompt = build_classification_prompt(
        STORY_POST,
        [{"slug": "story", "label": "Story", "description": "A narrative"},
         {"slug": "listicle", "label": "List", "description": "Enumerated advice"}],
    )
    assert "story: Story" in prompt
    assert "listicle: List" in prompt
    assert "existing_slug" in prompt


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("boxes", "box"),        # -es after a sibilant is a real plural
        ("listicles", "listicle"),
        ("pitches", "pitch"),
        ("takes", "take"),
    ],
)
def test_plural_stripping_knows_when_the_e_belongs_to_the_word(raw, expected):
    """Stripping -es unconditionally turned `listicles` into `listicl`, a slug
    that would then never match the type it was meant to be."""
    assert normalise_slug(raw) == expected


# ------------------------------------------------------------- merge pass --

@pytest.mark.asyncio
async def test_near_identical_types_are_proposed_for_merge(db_session):
    from app.services.post_type_service import merge_proposals

    await _seed(
        db_session,
        ("story", "Story", "A personal narrative with a turn", "seed"),
        ("personal_story", "Personal Story", "A personal narrative with a turn", "ai"),
        ("listicle", "List", "Enumerated advice as a countable set", "seed"),
    )

    proposals = await merge_proposals(db_session)
    pairs = {(p.loser_slug, p.winner_slug) for p in proposals}

    assert ("personal_story", "story") in pairs
    # The unrelated type is left alone.
    assert not any(p.loser_slug == "listicle" for p in proposals)


@pytest.mark.asyncio
async def test_a_seeded_type_always_survives_a_coined_one(db_session):
    """Even when the coined name is the better-used of the two — the seeds are
    the vocabulary the taxonomy is meant to have."""
    from app.services.post_type_service import merge_proposals

    await _seed(
        db_session,
        ("story", "Story", "A personal narrative with a turn", "seed"),
        ("personal_story", "Personal Story", "A personal narrative with a turn", "ai"),
    )
    coined = (
        await db_session.execute(select(PostType).where(PostType.slug == "personal_story"))
    ).scalar_one()
    coined.usage_count = 99
    await db_session.flush()

    proposal = (await merge_proposals(db_session))[0]
    assert proposal.loser_slug == "personal_story"
    assert proposal.winner_slug == "story"


@pytest.mark.asyncio
async def test_a_stale_coinage_with_no_neighbour_is_proposed_for_retirement(db_session):
    from app.services.post_type_service import merge_proposals

    await _seed(
        db_session,
        ("story", "Story", "A personal narrative with a turn", "seed"),
        ("quarterly_earnings", "Quarterly earnings",
         "Reports fiscal results to investors", "ai"),
    )
    orphan = (
        await db_session.execute(
            select(PostType).where(PostType.slug == "quarterly_earnings")
        )
    ).scalar_one()
    orphan.last_used_at = _now() - timedelta(days=300)
    await db_session.flush()

    proposals = await merge_proposals(db_session)
    retire = [p for p in proposals if p.loser_slug == "quarterly_earnings"]

    assert retire and retire[0].winner_slug is None
    assert "close to nothing else" in retire[0].reason


@pytest.mark.asyncio
async def test_merging_repoints_the_posts_and_folds_the_usage(db_session):
    from app.database.models import DiscoveredPost
    from app.services.post_type_service import merge_types

    await _seed(
        db_session,
        ("story", "Story", "A personal narrative", "seed"),
        ("personal_story", "Personal Story", "A narrative", "ai"),
    )
    survivor = (
        await db_session.execute(select(PostType).where(PostType.slug == "story"))
    ).scalar_one()
    loser = (
        await db_session.execute(select(PostType).where(PostType.slug == "personal_story"))
    ).scalar_one()
    survivor.usage_count, loser.usage_count = 4, 3

    db_session.add(DiscoveredPost(
        keyword="k", source="s", post_url="https://example.com/1",
        post_type_slug="personal_story",
    ))
    await db_session.flush()

    await merge_types(db_session, "personal_story", "story")
    await db_session.flush()

    post = (await db_session.execute(select(DiscoveredPost))).scalar_one()
    assert post.post_type_slug == "story"

    assert loser.active is False
    assert loser.merged_into_id == survivor.id
    # The survivor carries the history, so decay reflects the idea rather than
    # whichever name happened to hold it.
    assert survivor.usage_count == 7


@pytest.mark.asyncio
async def test_retiring_a_type_clears_it_from_its_posts(db_session):
    from app.database.models import DiscoveredPost
    from app.services.post_type_service import merge_types

    await _seed(db_session, ("one_off", "One off", "Coined once", "ai"))
    db_session.add(DiscoveredPost(
        keyword="k", source="s", post_url="https://example.com/2",
        post_type_slug="one_off",
    ))
    await db_session.flush()

    await merge_types(db_session, "one_off", None)
    await db_session.flush()

    post = (await db_session.execute(select(DiscoveredPost))).scalar_one()
    assert post.post_type_slug is None


@pytest.mark.asyncio
async def test_a_type_cannot_be_merged_into_itself(db_session):
    from app.services.post_type_service import merge_types

    await _seed(db_session, ("story", "Story", "A narrative", "seed"))
    with pytest.raises(ValueError, match="into itself"):
        await merge_types(db_session, "story", "story")


@pytest.mark.asyncio
async def test_merging_an_unknown_type_is_rejected(db_session):
    from app.services.post_type_service import merge_types

    await _seed(db_session, ("story", "Story", "A narrative", "seed"))
    with pytest.raises(ValueError, match="No such post type"):
        await merge_types(db_session, "nonsense", "story")
