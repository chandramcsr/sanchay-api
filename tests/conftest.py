import os

# Must be set before app.core.config imports Settings — env vars for tests.
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-not-for-production-use")
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.core.limiter import limiter
from app.main import app

# A single shared in-memory connection for the whole test session, via
# StaticPool — plain sqlite:///:memory: gives every new connection its
# own empty database, which breaks anything relying on data persisting
# across requests within one test.
engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = override_get_db


@pytest.fixture(autouse=True)
def _fresh_schema():
    Base.metadata.create_all(bind=engine)
    limiter.reset()
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def db_session():
    """
    Direct DB access for tests that need to set up state the HTTP API
    has no endpoint for (e.g. inserting a password reset token as if
    an email had just been sent). Shares the same in-memory database
    the app's overridden get_db() uses, via the same engine.
    """
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()
