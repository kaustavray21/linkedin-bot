"""Lineage must outlive the post it points at.

Discovered posts are hard-deleted at 30 days. If the lineage row only held a
foreign key, "which post was this drafted from" would go blank at exactly the
moment the record becomes interesting — and any draft built from that post would
lose the layout skeleton that keeps it reproducible.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import DiscoveredPost, DraftLineage, User
from app.services.discovery.service import delete_expired
from app.services.post_service import PostService


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


@pytest_asyncio.fixture
async def user(db_session: AsyncSession) -> User:
    u = User(linkedin_member_id="m-1", full_name="Ricky Ray")
    db_session.add(u)
    await db_session.flush()
    return u


@pytest_asyncio.fixture
async def exemplar(db_session: AsyncSession) -> DiscoveredPost:
    post = DiscoveredPost(
        keyword="shipping",
        source="ddg",
        post_url="https://www.linkedin.com/posts/source1",
        author_name="Priya Menon",
        content_text="Most outbound feels spammy for one reason.",
        layout_skeleton={"blocks": [{"lines": []}]},
        reactions=412,
        comments=37,
        metrics_source="measured",
        expires_at=_utcnow() + timedelta(days=30),
    )
    db_session.add(post)
    await db_session.flush()
    return post


@pytest.mark.asyncio
async def test_creating_a_draft_records_its_source(db_session, user, exemplar):
    service = PostService(db_session)
    post = await service.create_draft(
        user_id=user.id, content="my draft", exemplar_id=exemplar.id
    )

    lineage = (
        await db_session.execute(
            select(DraftLineage).where(DraftLineage.post_id == post.id)
        )
    ).scalar_one()

    assert lineage.discovered_post_id == exemplar.id
    assert lineage.exemplar_author == "Priya Menon"
    assert lineage.exemplar_reactions == 412
    assert lineage.exemplar_skeleton == {"blocks": [{"lines": []}]}
    assert exemplar.used_as_reference is True


@pytest.mark.asyncio
async def test_lineage_survives_the_source_being_deleted(db_session, user, exemplar):
    """The whole reason the snapshot is a copy rather than a join."""
    service = PostService(db_session)
    post = await service.create_draft(
        user_id=user.id, content="my draft", exemplar_id=exemplar.id
    )
    exemplar.expires_at = _utcnow() - timedelta(days=1)
    await db_session.flush()

    assert await delete_expired(db_session) == 1
    await db_session.flush()

    lineage = (
        await db_session.execute(
            select(DraftLineage).where(DraftLineage.post_id == post.id)
        )
    ).scalar_one()

    assert lineage.discovered_post_id is None, "FK should be nulled, not orphaned"
    assert lineage.exemplar_author == "Priya Menon", "the snapshot must remain"
    assert lineage.exemplar_url == "https://www.linkedin.com/posts/source1"
    assert lineage.exemplar_skeleton is not None, "draft must stay reproducible"

    remaining = (await db_session.execute(select(DiscoveredPost))).scalars().all()
    assert remaining == []


@pytest.mark.asyncio
async def test_unexpired_posts_are_left_alone(db_session, exemplar):
    assert await delete_expired(db_session) == 0
    assert (await db_session.execute(select(DiscoveredPost))).scalars().all()


@pytest.mark.asyncio
async def test_a_missing_exemplar_costs_the_lineage_not_the_draft(db_session, user):
    """A draft is worth more than its provenance."""
    service = PostService(db_session)
    post = await service.create_draft(
        user_id=user.id, content="my draft", exemplar_id=99999
    )
    assert post.id is not None
    assert (await db_session.execute(select(DraftLineage))).scalars().all() == []


@pytest.mark.asyncio
async def test_provenance_reaches_the_client(async_client, db_session, user, exemplar):
    service = PostService(db_session)
    await service.create_draft(
        user_id=user.id, content="my draft", exemplar_id=exemplar.id
    )

    response = await async_client.get(f"/posts/?user_id={user.id}")
    row = response.json()[0]
    assert row["source_author"] == "Priya Menon"
    assert row["source_url"] == "https://www.linkedin.com/posts/source1"
