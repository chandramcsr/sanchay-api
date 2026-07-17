import os

# Must be set before app.core.config imports Settings — env vars for tests.
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-not-for-production-use")
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.core.limiter import limiter
from app.main import app

# A single shared in-memory connection for the whole test session, via
# StaticPool — plain sqlite+aiosqlite:///:memory: gives every new
# connection its own empty database, which breaks anything relying on
# data persisting across requests within one test.
engine = create_async_engine(
    "sqlite+aiosqlite:///file::memory:?cache=shared&uri=true",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)


# SQLite does NOT enforce foreign key constraints by default -- unlike
# PostgreSQL (production), which always does. Without this, a real bug
# (deleting an account that still owned a group violated
# groups.created_by's FK on production, but every local/CI test
# passed silently, since SQLite never checked it at all) can slip
# through the entire test suite undetected. Every connection gets this
# turned on, matching production's actual behavior.
@event.listens_for(engine.sync_engine, "connect")
def _enable_sqlite_foreign_keys(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


TestingSessionLocal = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)


async def override_get_db():
    async with TestingSessionLocal() as db:
        yield db


app.dependency_overrides[get_db] = override_get_db


@pytest.fixture(scope="session", autouse=True)
async def _schema():
    """
    Schema created once for the whole test session, not per-test.
    Repeatedly dropping and recreating tables (the original approach)
    turned out to be unstable specifically with aiosqlite + StaticPool
    under a session-scoped event loop — occasional "no such table"
    errors on the second test onward, from DDL churn racing actual
    requests. Creating once and cleaning data between tests (below) is
    both the fix and the more standard pattern regardless.
    """
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest.fixture(autouse=True)
async def _clean_between_tests():
    limiter.reset()
    yield
    async with TestingSessionLocal() as db:
        for table in reversed(Base.metadata.sorted_tables):
            await db.execute(table.delete())
        await db.commit()


@pytest.fixture
async def client():
    """
    httpx.AsyncClient over ASGITransport — runs the app IN this same
    event loop, unlike Starlette's TestClient (which bridges to the
    ASGI app via a separate thread with its own event loop). That
    mismatch is exactly what broke things here: an aiosqlite
    connection created in one loop can't be safely used from another,
    so the schema-setup fixture and the actual request handlers ended
    up silently talking to two different in-memory databases. Async
    end to end avoids the whole problem at the root.
    """
    import httpx

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac


@pytest.fixture
async def db_session():
    """
    Direct DB access for tests that need to set up state the HTTP API
    has no endpoint for (e.g. inserting a password reset token as if
    an email had just been sent). Shares the same in-memory database
    the app's overridden get_db() uses, via the same engine.

    Async — any test using this fixture must itself be `async def`
    and use `await db_session.execute(select(...))` (SQLAlchemy 2.0
    style), not the old sync `.query()` API, which AsyncSession
    doesn't support at all.
    """
    async with TestingSessionLocal() as db:
        yield db


async def get_one(db_session, model, **filters):
    """`db.query(Model).filter_by(**filters).first()`, async-style."""
    from sqlalchemy import select

    stmt = select(model).filter_by(**filters)
    result = await db_session.execute(stmt)
    return result.scalar_one_or_none()


async def get_all(db_session, model, **filters):
    """`db.query(Model).filter_by(**filters).all()`, async-style."""
    from sqlalchemy import select

    stmt = select(model).filter_by(**filters)
    result = await db_session.execute(stmt)
    return list(result.scalars().all())


async def count_rows(db_session, model, **filters) -> int:
    """`db.query(Model).filter_by(**filters).count()`, async-style."""
    from sqlalchemy import select

    stmt = select(model).filter_by(**filters)
    result = await db_session.execute(stmt)
    return len(result.scalars().all())
