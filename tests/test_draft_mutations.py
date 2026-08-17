"""Bugs ①, ② and ⑤ from the draft plan's bug register.

All three are latent today only because nothing reopens a saved draft. Once the
draft library exists — with a 5-minute autosave and a save fired on browser
close — each becomes reachable on an ordinary edit:

  ① a save landing after publish rewrites a live post's stored text
  ② removing an image or un-scheduling silently does nothing
  ⑤ image provenance is produced, returned, and then dropped on the floor
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import (
    POST_STATUS_DRAFT,
    POST_STATUS_PUBLISHED,
    POST_STATUS_PUBLISHING,
    POST_STATUS_SCHEDULED,
)
from app.core.exceptions import ConflictException
from app.database.models import User
from app.services.post_service import PostService


@pytest_asyncio.fixture
async def user(db_session: AsyncSession) -> User:
    u = User(linkedin_member_id="member-1", full_name="Ricky Ray")
    db_session.add(u)
    await db_session.flush()
    return u


@pytest_asyncio.fixture
def service(db_session: AsyncSession) -> PostService:
    return PostService(db_session)


# ------------------------------------------------------- ① published guard --


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [POST_STATUS_PUBLISHED, POST_STATUS_PUBLISHING])
async def test_cannot_edit_a_post_that_has_gone_out(service, user, db_session, status):
    post = await service.create_draft(user_id=user.id, content="original text")
    await service.post_repo.update(post, status=status)

    with pytest.raises(ConflictException) as exc:
        await service.update_draft(
            post_id=post.id, user_id=user.id, content="rewritten after the fact"
        )

    assert exc.value.status_code == 409
    assert post.content == "original text", "a published post's text was rewritten"


@pytest.mark.asyncio
async def test_drafts_and_scheduled_posts_remain_editable(service, user):
    """The guard must not lock the states people actually edit."""
    draft = await service.create_draft(user_id=user.id, content="draft")
    updated = await service.update_draft(
        post_id=draft.id, user_id=user.id, content="edited"
    )
    assert updated.content == "edited"

    later = datetime(2027, 3, 3, 9, 0)
    scheduled = await service.create_draft(
        user_id=user.id, content="scheduled", scheduled_time=later
    )
    assert scheduled.status == POST_STATUS_SCHEDULED
    again = await service.update_draft(
        post_id=scheduled.id, user_id=user.id, content="still editable"
    )
    assert again.content == "still editable"


# ------------------------------------------------- ② omitted vs explicit null --


@pytest.mark.asyncio
async def test_omitting_a_field_leaves_it_untouched(service, user):
    post = await service.create_draft(
        user_id=user.id, content="body", image_url="/static/uploads/a.png"
    )
    updated = await service.update_draft(
        post_id=post.id, user_id=user.id, content="new body"
    )
    assert updated.image_url == "/static/uploads/a.png"


@pytest.mark.asyncio
async def test_explicit_null_clears_the_image(service, user):
    """The bug: an image could be attached but never removed."""
    post = await service.create_draft(
        user_id=user.id, content="body", image_url="/static/uploads/a.png"
    )
    updated = await service.update_draft(
        post_id=post.id, user_id=user.id, image_url=None
    )
    assert updated.image_url is None


@pytest.mark.asyncio
async def test_clearing_the_time_unschedules_the_post(service, user):
    """The old branch was unreachable, so a scheduled post could never go back."""
    post = await service.create_draft(
        user_id=user.id, content="body", scheduled_time=datetime.now() + timedelta(days=1)
    )
    assert post.status == POST_STATUS_SCHEDULED

    updated = await service.update_draft(
        post_id=post.id, user_id=user.id, scheduled_time=None
    )
    assert updated.scheduled_time is None
    assert updated.status == POST_STATUS_DRAFT


# ---------------------------------------------------------- ⑤ image_source --


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["ai", "upload", "url"])
async def test_image_source_is_persisted_on_create(service, user, source):
    post = await service.create_draft(
        user_id=user.id,
        content="body",
        image_url="/static/uploads/a.png",
        image_source=source,
    )
    assert post.image_source == source


@pytest.mark.asyncio
async def test_image_source_can_be_changed_and_cleared(service, user):
    post = await service.create_draft(
        user_id=user.id, content="body", image_url="/a.png", image_source="ai"
    )
    swapped = await service.update_draft(
        post_id=post.id, user_id=user.id, image_url="/b.png", image_source="upload"
    )
    assert swapped.image_source == "upload"

    cleared = await service.update_draft(
        post_id=post.id, user_id=user.id, image_url=None, image_source=None
    )
    assert cleared.image_url is None
    assert cleared.image_source is None


# ------------------------------------------------------------ through HTTP --


@pytest.mark.asyncio
async def test_round_trip_over_the_api(async_client, user):
    """The wire format matters as much as the service: exclude_unset is what
    carries the omitted/null distinction across the boundary."""
    created = await async_client.post(
        f"/posts/?user_id={user.id}",
        json={"content": "hello", "image_url": "/x.png", "image_source": "ai"},
    )
    assert created.status_code == 201
    post_id = created.json()["id"]
    assert created.json()["image_source"] == "ai"

    # Omitted -> untouched
    kept = await async_client.put(
        f"/posts/{post_id}?user_id={user.id}", json={"content": "hello again"}
    )
    assert kept.json()["image_url"] == "/x.png"
    assert kept.json()["image_source"] == "ai"

    # Explicit null -> cleared
    cleared = await async_client.put(
        f"/posts/{post_id}?user_id={user.id}", json={"image_url": None, "image_source": None}
    )
    assert cleared.json()["image_url"] is None
    assert cleared.json()["image_source"] is None
