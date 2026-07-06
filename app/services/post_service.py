from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import POST_STATUS_DRAFT, POST_STATUS_FAILED, POST_STATUS_PUBLISHED, POST_STATUS_PUBLISHING, POST_STATUS_SCHEDULED
from app.core.exceptions import ValidationException, NotFoundException
from app.core.logger import get_logger
from app.database.models import Post
from app.repositories.post_repository import PostRepository
from app.services.linkedin_service import LinkedInService
from app.services.token_service import TokenService

log = get_logger()


class PostService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.post_repo = PostRepository(session)
        self.token_service = TokenService(session)

    async def create_draft(self, user_id: int, content: str, image_url: str | None = None, scheduled_time=None) -> Post:
        status = POST_STATUS_SCHEDULED if scheduled_time else POST_STATUS_DRAFT
        post = await self.post_repo.create(
            user_id=user_id,
            content=content,
            image_url=image_url,
            status=status,
            scheduled_time=scheduled_time,
        )
        log.info("Draft created", post_id=post.id, user_id=user_id, status=status)
        return post

    async def update_draft(self, post_id: int, user_id: int, content: str | None = None, image_url: str | None = None, scheduled_time=None) -> Post:
        post = await self.post_repo.get_by_id(post_id)
        if not post or post.user_id != user_id:
            raise NotFoundException("Post not found")

        updates: dict = {}
        if content is not None:
            updates["content"] = content
        if image_url is not None:
            updates["image_url"] = image_url
        if scheduled_time is not None:
            updates["scheduled_time"] = scheduled_time
            updates["status"] = POST_STATUS_SCHEDULED if scheduled_time else POST_STATUS_DRAFT

        if updates:
            post = await self.post_repo.update(post, **updates)

        return post

    async def publish_post(self, post_id: int, user_id: int) -> dict:
        post = await self.post_repo.get_by_id(post_id)
        if not post or post.user_id != user_id:
            raise NotFoundException("Post not found")

        if post.status == POST_STATUS_PUBLISHED:
            raise ValidationException("Post already published")

        await self.post_repo.update(post, status=POST_STATUS_PUBLISHING)

        try:
            access_token = await self.token_service.get_valid_access_token(user_id)
            linkedin_service = LinkedInService(access_token)

            from app.database.models import User
            from sqlalchemy import select
            result = await self.session.execute(select(User).where(User.id == user_id))
            user = result.scalar_one_or_none()
            if not user:
                raise NotFoundException("User not found")

            if post.image_url:
                log.info("Uploading image to LinkedIn first", image_url=post.image_url)
                image_urn = await linkedin_service.upload_image(
                    author=user.linkedin_member_id,
                    image_local_path=post.image_url
                )
                linkedin_response = await linkedin_service.create_image_post(
                    author=user.linkedin_member_id,
                    content=post.content,
                    image_urn=image_urn,
                )
            else:
                linkedin_response = await linkedin_service.create_post(
                    author=user.linkedin_member_id,
                    content=post.content,
                )

            linkedin_post_id = linkedin_response.get("id", "")
            from datetime import datetime, timezone
            await self.post_repo.update(
                post,
                status=POST_STATUS_PUBLISHED,
                linkedin_post_id=linkedin_post_id,
                published_time=datetime.now(timezone.utc).replace(tzinfo=None),
            )

            log.info("Post published", post_id=post.id, linkedin_post_id=linkedin_post_id)
            return {"linkedin_post_id": linkedin_post_id}

        except Exception as e:
            await self.post_repo.update(post, status=POST_STATUS_FAILED)
            log.error("Post publish failed", post_id=post.id, error=str(e))
            raise

    async def get_user_posts(self, user_id: int) -> Sequence[Post]:
        return await self.post_repo.get_by_user_id(user_id)

    async def get_post(self, post_id: int, user_id: int) -> Post:
        post = await self.post_repo.get_by_id(post_id)
        if not post or post.user_id != user_id:
            raise NotFoundException("Post not found")
        return post

    async def delete_post(self, post_id: int, user_id: int) -> None:
        post = await self.post_repo.get_by_id(post_id)
        if not post or post.user_id != user_id:
            raise NotFoundException("Post not found")
        await self.post_repo.delete(post)

    async def retry_failed(self, post_id: int, user_id: int) -> dict:
        post = await self.post_repo.get_by_id(post_id)
        if not post or post.user_id != user_id:
            raise NotFoundException("Post not found")
        if post.status != POST_STATUS_FAILED:
            raise ValidationException("Post is not in failed status")
        return await self.publish_post(post_id, user_id)
