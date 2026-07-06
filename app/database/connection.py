from __future__ import annotations

from collections.abc import AsyncGenerator
from functools import lru_cache

from sqlalchemy import URL
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings


@lru_cache
def _get_database_url() -> URL:
    return URL.create(
        "mysql+aiomysql",
        username=settings.mysql_user,
        password=settings.mysql_password,
        host=settings.mysql_host,
        port=settings.mysql_port,
        database=settings.mysql_database,
    )


@lru_cache
def get_engine():
    return create_async_engine(
        _get_database_url(),
        echo=settings.debug,
        pool_pre_ping=True,
    )


@lru_cache
def get_session_factory():
    return async_sessionmaker(
        get_engine(),
        class_=AsyncSession,
        expire_on_commit=False,
    )


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
