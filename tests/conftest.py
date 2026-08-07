import os
from datetime import datetime, timedelta, timezone

# Must be set before app.core.config imports Settings — env vars for tests.
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-not-for-production-use")
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("SANCHAY_APP_DATABASE_URL", "sqlite:///:memory:")

# A real RSA keypair, generated once per test run -- not a fake/short
# string. get_clerk_user_id does real RS256 signature verification, so
# tests need a real key exercising the real verification path, not a
# mock that bypasses it and would miss a genuine bug in that code.
# CLERK_JWT_KEY (the public half) must be set before config.py builds
# `settings`, same reasoning as JWT_SECRET_KEY above. The private half
# stays in this file only, for tests/conftest.py's own token-signing
# helper (see clerk_auth() below) -- it never needs to leave here.
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

_clerk_test_private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_clerk_test_private_pem = _clerk_test_private_key.private_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PrivateFormat.PKCS8,
    encryption_algorithm=serialization.NoEncryption(),
).decode()
_clerk_test_public_pem = (
    _clerk_test_private_key.public_key()
    .public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    .decode()
)
os.environ.setdefault("CLERK_JWT_KEY", _clerk_test_public_pem)
os.environ.setdefault("CLERK_AUTHORIZED_PARTIES", "http://testserver")

import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.core.database import Base, SanchayAppBase, get_db, get_sanchay_app_db
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


# Second in-memory database, mirroring the setup above exactly (same
# StaticPool reasoning, same FK-enforcement pragma) -- for the
# accounts/transactions/budgets models on SanchayAppBase, which are a
# genuinely separate database from everything above this point.
sanchay_app_engine = create_async_engine(
    "sqlite+aiosqlite:///file::memory:?cache=shared&uri=true&sanchay_app=1",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)


@event.listens_for(sanchay_app_engine.sync_engine, "connect")
def _enable_sanchay_app_sqlite_foreign_keys(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


SanchayAppTestingSessionLocal = async_sessionmaker(bind=sanchay_app_engine, class_=AsyncSession, expire_on_commit=False)


async def override_get_sanchay_app_db():
    async with SanchayAppTestingSessionLocal() as db:
        yield db


app.dependency_overrides[get_sanchay_app_db] = override_get_sanchay_app_db


def stub_embed_fn(text: str) -> list[float]:
    """
    A deterministic, dependency-free stand-in for the real embedding
    model (sentence-transformers isn't installed in this environment
    -- see embedding_service.py's module docstring). This is a small
    "hashing trick" bag-of-words vector, not a random one: texts that
    share more words hash into more of the same buckets and end up
    with genuinely higher cosine similarity, which is what lets
    retrieval tests verify real behavior (a relevant transaction
    actually ranks above an irrelevant one) rather than just asserting
    "a vector of the right length came back."
    """
    dim = 64
    vector = [0.0] * dim
    for word in text.lower().split():
        vector[hash(word) % dim] += 1.0
    return vector


def stub_generate_fn(system: str, user: str) -> str:
    """
    A minimal stand-in for the real Claude call. Echoes back the
    passage labels it was given so citation-verification tests have
    something realistic to check against, without any network call or
    API key.
    """
    import re

    labels = re.findall(r"\[P\d+\]", user)
    cite = labels[0] if labels else ""
    return f"Based on the transactions provided {cite}.".strip()


from app.routers.ai import get_generate_fn  # noqa: E402
from app.services.embedding_service import get_document_embed_fn, get_query_embed_fn  # noqa: E402

app.dependency_overrides[get_document_embed_fn] = lambda: stub_embed_fn
app.dependency_overrides[get_query_embed_fn] = lambda: stub_embed_fn
app.dependency_overrides[get_generate_fn] = lambda: stub_generate_fn

# Import registers Account/Transaction/Budget on SanchayAppBase.metadata
# -- needed before _schema's create_all below, same reasoning as
# `from app.models import user` in app/main.py itself.
from app.models.account import Account  # noqa: E402,F401
from app.models.budget import Budget  # noqa: E402,F401
from app.models.transaction import Transaction  # noqa: E402,F401


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
    async with sanchay_app_engine.begin() as conn:
        await conn.run_sync(SanchayAppBase.metadata.create_all)
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    async with sanchay_app_engine.begin() as conn:
        await conn.run_sync(SanchayAppBase.metadata.drop_all)


@pytest.fixture(autouse=True)
async def _clean_between_tests():
    limiter.reset()
    yield
    async with TestingSessionLocal() as db:
        for table in reversed(Base.metadata.sorted_tables):
            await db.execute(table.delete())
        await db.commit()
    async with SanchayAppTestingSessionLocal() as db:
        for table in reversed(SanchayAppBase.metadata.sorted_tables):
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


@pytest.fixture
async def sanchay_app_db_session():
    """Same as db_session, but for the second (Sanchaydb) database --
    accounts/transactions/budgets."""
    async with SanchayAppTestingSessionLocal() as db:
        yield db


@pytest.fixture
def clerk_auth():
    """
    Returns a function that signs a Clerk-shaped RS256 token with the
    test private key (generated at the top of this file) and returns
    it as an Authorization header dict, ready to pass straight to a
    test client call. Real RS256 signing against a real keypair -- not
    a mock of get_clerk_user_id -- so tests exercise the actual
    verification code path (signature check, azp check, exp check),
    the same code that runs against real Clerk tokens in production.

    A fixture, not a plain importable function -- `from tests.conftest
    import clerk_auth` in a test file creates a second, separate import
    of this module (pytest's own auto-discovery imports conftest.py
    under a different module identity than an explicit package-
    qualified import does), which re-runs the RSA keygen above and
    produces a *different* keypair than the one settings.clerk_jwt_key
    actually holds -- signed tokens then fail verification with no
    indication why. Fixture injection sidesteps the whole problem: no
    import, no second module identity, no key mismatch.
    """

    def _make(user_id: str, *, azp: str = "http://testserver", expired: bool = False) -> dict[str, str]:
        from jose import jwt as jose_jwt

        now = datetime.now(timezone.utc)
        payload = {
            "sub": user_id,
            "azp": azp,
            "iat": now,
            "exp": now - timedelta(minutes=5) if expired else now + timedelta(minutes=60),
        }
        token = jose_jwt.encode(payload, _clerk_test_private_pem, algorithm="RS256")
        return {"Authorization": f"Bearer {token}"}

    return _make


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
