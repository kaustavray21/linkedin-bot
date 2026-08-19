"""End-to-end check of the post-types endpoints through the real app."""
import pytest

@pytest.mark.asyncio
async def test_post_type_endpoints_round_trip(async_client, db_session):
    from app.database.models import PostType

    for slug, origin in (("story", "seed"), ("personal_story", "ai")):
        db_session.add(PostType(slug=slug, label=slug.replace("_", " ").title(),
                                description="A personal narrative with a turn",
                                origin=origin))
    await db_session.flush()

    listed = (await async_client.get("/post-types")).json()
    assert {t["slug"] for t in listed} == {"story", "personal_story"}

    proposals = (await async_client.get("/post-types/merge-proposals")).json()
    assert any(p["loser_slug"] == "personal_story" and p["winner_slug"] == "story"
               for p in proposals)

    merged = await async_client.post("/post-types/merge",
                                     json={"loser_slug": "personal_story",
                                           "winner_slug": "story"})
    assert merged.status_code == 200, merged.text
    assert merged.json()["merged_into"] == "story"

    after = (await async_client.get("/post-types")).json()
    assert {t["slug"] for t in after} == {"story"}

    bad = await async_client.post("/post-types/merge",
                                  json={"loser_slug": "ghost", "winner_slug": "story"})
    assert bad.status_code == 400
