from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from app.database.models import DiscoveredPost
from app.services.hashtag_service import extract_tags, is_generic, remix_hashtags
from app.services.remix_service import pick_exemplar, remix_from_post

EXEMPLAR_TEXT = """I shipped it.

Twice.

Here is the single lesson that both attempts taught me about building.

#BuildInPublic #FounderLife #AI"""


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _post(**kw):
    defaults = dict(
        keyword="shipping",
        source="ddg",
        post_url=f"https://www.linkedin.com/posts/x-activity-{kw.get('rank', 1)}",
        content_text=EXEMPLAR_TEXT,
        hashtags=["#BuildInPublic", "#FounderLife", "#AI"],
        engagement_score=1.0,
        fetched_at=_now(),
    )
    defaults.pop("rank", None)
    kw.pop("rank", None)
    defaults.update(kw)
    return DiscoveredPost(**defaults)


# --------------------------------------------------------------- hashtags --

def test_generic_tags_are_recognised():
    assert is_generic("#AI")
    assert is_generic("#python")
    assert not is_generic("#BuildInPublic")


@pytest.mark.asyncio
async def test_distinctive_source_tags_are_never_reproduced():
    """Enforced in code, not merely requested in the prompt — this is the whole
    point of the feature."""
    ai = AsyncMock()
    ai.generate_with_gemini.return_value = "#BuildInPublic #FounderLife #AI"

    out = await remix_hashtags(
        ["#BuildInPublic", "#FounderLife", "#AI"], topic="shipping software", ai_service=ai
    )

    assert "#BuildInPublic" not in out
    assert "#FounderLife" not in out


@pytest.mark.asyncio
async def test_generic_tags_may_pass_through():
    """#AI names the field. Dropping it costs reach and gains no originality."""
    ai = AsyncMock()
    ai.generate_with_gemini.return_value = "#AI #ShippingLoud #MakerLife"

    out = await remix_hashtags(["#AI", "#BuildInPublic"], topic="ai agents", ai_service=ai)
    assert "#AI" in out


@pytest.mark.asyncio
async def test_count_matches_the_source_by_default():
    ai = AsyncMock()
    ai.generate_with_gemini.return_value = "#One #Two #Three #Four #Five"

    out = await remix_hashtags(["#A", "#B", "#C"], topic="x", ai_service=ai)
    assert len(out) == 3


@pytest.mark.asyncio
async def test_model_failure_falls_back_without_copying():
    ai = AsyncMock()
    ai.generate_with_gemini.side_effect = RuntimeError("gemini down")

    out = await remix_hashtags(
        ["#BuildInPublic", "#AI"], topic="shipping software fast", ai_service=ai
    )
    assert "#BuildInPublic" not in out


@pytest.mark.asyncio
async def test_placeholder_output_is_not_mistaken_for_tags():
    ai = AsyncMock()
    ai.generate_with_gemini.return_value = (
        "Excited to share my latest insights on x! Stay tuned for more updates."
    )
    out = await remix_hashtags(["#BuildInPublic"], topic="shipping fast", ai_service=ai)
    assert all(t.startswith("#") for t in out)
    assert "#BuildInPublic" not in out


def test_extract_tags_finds_all():
    assert extract_tags("a #One b #Two") == ["#One", "#Two"]


# -------------------------------------------------------------- exemplars --

@pytest.mark.asyncio
async def test_highest_scoring_readable_post_is_chosen(db_session):
    db_session.add_all([
        _post(post_url="https://www.linkedin.com/posts/a-activity-1", engagement_score=1.0),
        _post(post_url="https://www.linkedin.com/posts/b-activity-2", engagement_score=9.0),
    ])
    await db_session.flush()

    chosen = await pick_exemplar(db_session, keyword="shipping")
    assert chosen.engagement_score == 9.0


@pytest.mark.asyncio
async def test_unreadable_posts_are_skipped_even_when_top_ranked(db_session):
    """A post with no extractable body has no shape to clone, however well it
    performed."""
    db_session.add_all([
        _post(post_url="https://www.linkedin.com/posts/a-activity-1",
              content_text=None, engagement_score=99.0),
        _post(post_url="https://www.linkedin.com/posts/b-activity-2", engagement_score=2.0),
    ])
    await db_session.flush()

    chosen = await pick_exemplar(db_session, keyword="shipping")
    assert chosen.engagement_score == 2.0


@pytest.mark.asyncio
async def test_purged_posts_are_not_offered_as_exemplars(db_session):
    db_session.add(_post(purged_at=_now(), content_text=None, engagement_score=50.0))
    await db_session.flush()
    assert await pick_exemplar(db_session, keyword="shipping") is None


# ------------------------------------------------------------------ remix --

@pytest.mark.asyncio
async def test_remix_clones_shape_and_replaces_words(db_session, monkeypatch):
    exemplar = _post()
    db_session.add(exemplar)
    await db_session.flush()

    fresh = (
        "We launched today.\n\nFinally.\n\n"
        "One realisation changed how our whole team approaches new product work.\n\n"
        "#ShipFast #MakerLife #AI"
    )

    async def fake_generate(**kwargs):
        from app.services.similarity_service import check_similarity
        return fresh, check_similarity(fresh, EXEMPLAR_TEXT)

    monkeypatch.setattr("app.services.remix_service.generate_with_layout", fake_generate)
    monkeypatch.setattr(
        "app.services.remix_service.remix_hashtags",
        AsyncMock(return_value=["#ShipFast", "#MakerLife", "#AI"]),
    )

    result = await remix_from_post(
        db_session, topic="launching", exemplar=exemplar, with_image=False
    )

    assert result.similarity.passed
    assert result.exemplar_url == exemplar.post_url
    assert "#BuildInPublic" not in result.full_text


@pytest.mark.asyncio
async def test_image_failure_does_not_lose_the_draft(db_session, monkeypatch):
    """Publishing never required an image, so a fal.ai outage must not discard
    a perfectly good text draft."""
    exemplar = _post()
    db_session.add(exemplar)
    await db_session.flush()

    text = "A.\n\nB.\n\nC is a complete and quite original closing sentence here.\n\n#New #Tags #AI"

    async def fake_generate(**kwargs):
        from app.services.similarity_service import check_similarity
        return text, check_similarity(text, EXEMPLAR_TEXT)

    monkeypatch.setattr("app.services.remix_service.generate_with_layout", fake_generate)
    monkeypatch.setattr(
        "app.services.remix_service.remix_hashtags", AsyncMock(return_value=["#New"])
    )
    monkeypatch.setattr(
        "app.services.remix_service.generate_style_matched_image",
        AsyncMock(return_value=(None, None)),
    )

    result = await remix_from_post(
        db_session, topic="x", exemplar=exemplar, with_image=True
    )

    assert result.text == text
    assert result.image_url is None
    assert any("Image generation was unavailable" in n for n in result.notes)


@pytest.mark.asyncio
async def test_remixing_an_unreadable_post_is_refused(db_session):
    exemplar = _post(content_text=None)
    db_session.add(exemplar)
    await db_session.flush()

    with pytest.raises(ValueError, match="no readable text"):
        await remix_from_post(db_session, topic="x", exemplar=exemplar, with_image=False)


def test_full_text_appends_hashtags_once():
    from app.services.remix_service import RemixResult

    result = RemixResult(text="Body here.", hashtags=["#One", "#Two"])
    assert result.full_text == "Body here.\n\n#One #Two"

    already = RemixResult(text="Body here.\n\n#One #Two", hashtags=["#One", "#Two"])
    assert already.full_text.count("#One") == 1


def test_model_written_hashtag_block_is_replaced_not_duplicated():
    """Observed on a live run: the model wrote its own trailing tags (including
    a near-copy of a source tag) and the remixed set was appended below them,
    producing two blocks and smuggling an unpoliced tag into the post."""
    from app.services.remix_service import RemixResult

    result = RemixResult(
        text="Keep sharing your story.\n\n#buildinpublic #growthmindset #community",
        hashtags=["#openstartup", "#indiehackers", "#solopreneur"],
    )
    out = result.full_text

    assert out.count("#") == 3
    assert "#buildinpublic" not in out
    assert out.endswith("#openstartup #indiehackers #solopreneur")


def test_stripping_leaves_prose_ending_in_a_single_tag_alone():
    from app.services.hashtag_service import strip_trailing_hashtag_block

    text = "We shipped it today #proud"
    assert strip_trailing_hashtag_block(text) == text


def test_stripping_removes_multiple_trailing_tag_blocks():
    from app.services.hashtag_service import strip_trailing_hashtag_block

    text = "Body.\n\n#One #Two\n\n#Three"
    assert strip_trailing_hashtag_block(text) == "Body."
