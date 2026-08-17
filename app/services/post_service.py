from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import POST_STATUS_DRAFT, POST_STATUS_FAILED, POST_STATUS_PUBLISHED, POST_STATUS_PUBLISHING, POST_STATUS_SCHEDULED
from app.core.exceptions import ConflictException, ValidationException, NotFoundException
from app.core.logger import get_logger
from app.database.models import Post
from app.repositories.post_repository import PostRepository
from app.services.linkedin_service import LinkedInService
from app.services.token_service import TokenService

log = get_logger()


class _Unset:
    """Marker for "this field was not supplied".

    `None` cannot carry that meaning here: clearing an image and leaving it
    alone are different operations, and both arrive as None. The old
    `if value is not None` treated them identically, which is why an image
    could never be removed and a scheduled post could never be un-scheduled.
    """

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "UNSET"


UNSET = _Unset()

# Editing a post that is already out, or on its way out, is not an input error
# the caller can fix by retrying with a better payload — the window simply
# closed. Guarded in the service rather than only in the UI because an autosave
# timer or an in-flight request can land after publish, and rewriting the row
# then would leave history showing text that never appeared on LinkedIn.
_UNEDITABLE_STATUSES = (POST_STATUS_PUBLISHED, POST_STATUS_PUBLISHING)


class PostService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.post_repo = PostRepository(session)
        self.token_service = TokenService(session)

    async def create_draft(
        self,
        user_id: int,
        content: str,
        image_url: str | None = None,
        scheduled_time=None,
        image_source: str | None = None,
        exemplar_id: int | None = None,
    ) -> Post:
        status = POST_STATUS_SCHEDULED if scheduled_time else POST_STATUS_DRAFT
        post = await self.post_repo.create(
            user_id=user_id,
            content=content,
            image_url=image_url,
            image_source=image_source,
            status=status,
            scheduled_time=scheduled_time,
        )
        if exemplar_id is not None:
            # Best effort: a draft is more valuable than its provenance, so a
            # missing exemplar loses the lineage rather than the post.
            from sqlalchemy import select as _select

            from app.database.models import DiscoveredPost
            from app.services.discovery.service import record_lineage

            exemplar = (
                await self.session.execute(
                    _select(DiscoveredPost).where(DiscoveredPost.id == exemplar_id)
                )
            ).scalar_one_or_none()
            if exemplar is not None:
                await record_lineage(self.session, post.id, exemplar)
            else:
                log.warning("Exemplar vanished before lineage was recorded",
                            exemplar_id=exemplar_id, post_id=post.id)

        log.info("Draft created", post_id=post.id, user_id=user_id, status=status)
        return post

    async def update_draft(
        self,
        post_id: int,
        user_id: int,
        content=UNSET,
        image_url=UNSET,
        scheduled_time=UNSET,
        image_source=UNSET,
    ) -> Post:
        """Update a draft. Omitted fields are untouched; explicit None clears.

        Callers that pass nothing for a field keep the previous behaviour
        exactly — the difference is only that passing None now means something.
        """
        post = await self.post_repo.get_by_id(post_id)
        if not post or post.user_id != user_id:
            raise NotFoundException("Post not found")

        if post.status in _UNEDITABLE_STATUSES:
            raise ConflictException(
                f"This post is {post.status} and can no longer be edited."
            )

        updates: dict = {}
        if content is not UNSET:
            updates["content"] = content
        if image_url is not UNSET:
            updates["image_url"] = image_url
        if image_source is not UNSET:
            updates["image_source"] = image_source
        if scheduled_time is not UNSET:
            updates["scheduled_time"] = scheduled_time
            # Clearing the time un-schedules the post; setting one schedules it.
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
