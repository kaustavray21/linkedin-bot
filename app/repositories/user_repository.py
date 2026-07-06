from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import User


class UserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, linkedin_member_id: str, full_name: str | None = None, email: str | None = None, profile_picture: str | None = None) -> User:
        user = User(
            linkedin_member_id=linkedin_member_id,
            full_name=full_name,
            email=email,
            profile_picture=profile_picture,
        )
        self.session.add(user)
        await self.session.flush()
        await self.session.refresh(user)
        return user

    async def get_by_id(self, user_id: int) -> User | None:
        result = await self.session.execute(select(User).where(User.id == user_id))
        return result.scalar_one_or_none()

    async def get_by_linkedin_id(self, linkedin_member_id: str) -> User | None:
        result = await self.session.execute(select(User).where(User.linkedin_member_id == linkedin_member_id))
        return result.scalar_one_or_none()

    async def update(self, user: User, **kwargs) -> User:
        for key, value in kwargs.items():
            setattr(user, key, value)
        await self.session.flush()
        await self.session.refresh(user)
        return user
