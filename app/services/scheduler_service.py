from __future__ import annotations

from collections.abc import Sequence

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ValidationException, NotFoundException
from app.core.logger import get_logger
from app.database.models import Schedule
from app.repositories.schedule_repository import ScheduleRepository

log = get_logger(tag="scheduler")


class SchedulerService:
    def __init__(self, scheduler: AsyncIOScheduler | None = None) -> None:
        self.scheduler = scheduler or AsyncIOScheduler()
        self._running = False

    async def create_schedule(self, session: AsyncSession, user_id: int, cron_expression: str) -> Schedule:
        repo = ScheduleRepository(session)
        if not self._validate_cron(cron_expression):
            raise ValidationException("Invalid cron expression")

        schedule = await repo.create(user_id=user_id, cron_expression=cron_expression)
        log.info("Schedule created", schedule_id=schedule.id, user_id=user_id)
        return schedule

    async def get_user_schedules(self, session: AsyncSession, user_id: int) -> Sequence[Schedule]:
        repo = ScheduleRepository(session)
        return await repo.get_by_user_id(user_id)

    async def delete_schedule(self, session: AsyncSession, schedule_id: int, user_id: int) -> None:
        repo = ScheduleRepository(session)
        schedule = await repo.get_by_id(schedule_id)
        if not schedule or schedule.user_id != user_id:
            raise NotFoundException("Schedule not found")
        await repo.delete(schedule)
        log.info("Schedule deleted", schedule_id=schedule_id, user_id=user_id)

    def start(self) -> None:
        if not self._running:
            self.scheduler.add_job(
                self._poll_scheduled_posts,
                "interval",
                minutes=1,
                id="poll_scheduled_posts",
                replace_existing=True,
            )
            self.scheduler.start()
            self._running = True
            log.info("Scheduler started")

    async def _poll_scheduled_posts(self) -> None:
        from app.database.connection import get_session_factory
        session_factory = get_session_factory()
        async with session_factory() as session:
            try:
                from app.repositories.post_repository import PostRepository
                from app.services.post_service import PostService
                post_repo = PostRepository(session)
                posts = await post_repo.get_due_scheduled_posts()
                if posts:
                    log.info(f"Polling: found {len(posts)} due scheduled posts to publish")
                    post_service = PostService(session)
                    for post in posts:
                        try:
                            await post_service.publish_post(post.id, post.user_id)
                            await session.commit()
                        except Exception as e:
                            log.error(f"Error publishing scheduled post {post.id}: {e}")
                            await session.rollback()
            except Exception as e:
                log.error(f"Error in scheduled post polling: {e}")


    def stop(self) -> None:
        if self._running:
            self.scheduler.shutdown(wait=False)
            self._running = False
            log.info("Scheduler stopped")

    def _validate_cron(self, expression: str) -> bool:
        from apscheduler.triggers.cron import CronTrigger
        try:
            CronTrigger.from_crontab(expression)
            return True
        except (ValueError, TypeError):
            return False
