from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import POST_STATUS_SCHEDULED
from app.database.models import Post


class PostRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(
        self,
        user_id: int,
        content: str,
        status: str = "draft",
        image_url: str | None = None,
        scheduled_time=None,
    ) -> Post:
        post = Post(
            user_id=user_id,
            content=content,
            image_url=image_url,
            status=status,
            scheduled_time=scheduled_time,
        )
        self.session.add(post)
        await self.session.flush()
        await self.session.refresh(post)
        return post

    async def get_by_id(self, post_id: int) -> Post | None:
        result = await self.session.execute(select(Post).where(Post.id == post_id))
        return result.scalar_one_or_none()

    async def get_by_user_id(self, user_id: int) -> Sequence[Post]:
        result = await self.session.execute(
            select(Post).where(Post.user_id == user_id).order_by(Post.created_at.desc())
        )
        return result.scalars().all()

    async def get_due_scheduled_posts(self) -> Sequence[Post]:
        """Return all scheduled posts whose scheduled_time has passed."""
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        result = await self.session.execute(
            select(Post).where(
                Post.status == POST_STATUS_SCHEDULED,
                Post.scheduled_time <= now,
            )
        )
        return result.scalars().all()

    async def update(self, post: Post, **kwargs) -> Post:
        for key, value in kwargs.items():
            setattr(post, key, value)
        await self.session.flush()
        await self.session.refresh(post)
        return post

    async def delete(self, post: Post) -> None:
        await self.session.delete(post)
        await self.session.flush()
