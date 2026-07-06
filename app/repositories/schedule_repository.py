from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Schedule


class ScheduleRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, user_id: int, cron_expression: str) -> Schedule:
        schedule = Schedule(
            user_id=user_id,
            cron_expression=cron_expression,
        )
        self.session.add(schedule)
        await self.session.flush()
        await self.session.refresh(schedule)
        return schedule

    async def get_by_id(self, schedule_id: int) -> Schedule | None:
        result = await self.session.execute(select(Schedule).where(Schedule.id == schedule_id))
        return result.scalar_one_or_none()

    async def get_by_user_id(self, user_id: int) -> Sequence[Schedule]:
        result = await self.session.execute(
            select(Schedule).where(Schedule.user_id == user_id).order_by(Schedule.created_at.desc())
        )
        return result.scalars().all()

    async def update(self, schedule: Schedule, **kwargs) -> Schedule:
        for key, value in kwargs.items():
            setattr(schedule, key, value)
        await self.session.flush()
        await self.session.refresh(schedule)
        return schedule

    async def delete(self, schedule: Schedule) -> None:
        await self.session.delete(schedule)
        await self.session.flush()
