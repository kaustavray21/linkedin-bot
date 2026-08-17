from __future__ import annotations

from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database.base import Base
from app.database.connection import get_session
from app.main import app


@pytest.fixture
def mock_session() -> MagicMock:
    session = AsyncMock(spec=AsyncSession)
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    session.close = AsyncMock()
    session.flush = AsyncMock()
    return session


@pytest_asyncio.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    """A real session against a per-test in-memory SQLite database.

    The production engine is built once via @lru_cache (app/database/connection.py:24),
    so its aiomysql pool binds to whichever event loop touched it first. pytest-asyncio
    gives every test a fresh loop, so reusing that engine hands tests connections owned
    by a dead loop — which surfaces as "Event loop is closed" or an outright hang.
    Building the engine inside the test's own loop is what keeps the pool valid.
    """
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session

    await engine.dispose()


@pytest_asyncio.fixture
async def async_client(db_session: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    """Test client whose request-scoped DB session is the in-memory one above.

    Without this override every endpoint under test would reach for the real MySQL
    engine and inherit the cross-loop pool problem described in db_session.
    """

    async def _override_get_session() -> AsyncGenerator[AsyncSession, None]:
        yield db_session

    app.dependency_overrides[get_session] = _override_get_session
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    app.dependency_overrides.clear()
