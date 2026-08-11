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


@pytest_asyncio.fixture
async def seeded_references(db_session: AsyncSession) -> AsyncSession:
    """Two reference profiles with deliberately distinct layout shapes.

    sub1 is short_punchy (one-line paragraphs); sub2 is flowing prose. The contrast
    is the point: it is what makes an averaging bug visible in assertions.
    """
    from app.database.models import ReferencePost, ReferenceProfile

    punchy = ReferenceProfile(slug="sub1", profile_url="https://linkedin.com/in/sub1")
    flowing = ReferenceProfile(slug="sub2", profile_url="https://linkedin.com/in/sub2")
    db_session.add_all([punchy, flowing])
    await db_session.flush()

    db_session.add_all(
        [
            ReferencePost(
                profile_id=punchy.id,
                filename="ref-1.txt",
                full_text="I failed.\n\nTwice.\n\nHere is what it taught me about shipping.\n\n#BuildInPublic #Startups",
            ),
            ReferencePost(
                profile_id=punchy.id,
                filename="ref-2.txt",
                full_text="Stop optimising.\n\nStart shipping.\n\nYour users cannot use a plan.\n\n#Product #Founders",
            ),
            ReferencePost(
                profile_id=flowing.id,
                filename="ref-1.txt",
                full_text=(
                    "When I first started building software professionally I believed that the "
                    "hardest part would be the code itself, but over the years I have come to "
                    "understand that the genuine difficulty lies somewhere else entirely.\n\n"
                    "The difficulty is in deciding what deserves to be built at all, and that is "
                    "a judgement no framework will ever make on your behalf.\n\n#Engineering"
                ),
            ),
        ]
    )
    await db_session.flush()
    return db_session
