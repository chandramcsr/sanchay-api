from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.core.config import settings

# check_same_thread only matters for SQLite (used in tests / local dev
# without Postgres); harmless to pass for Postgres since it's ignored.
connect_args = {"check_same_thread": False} if settings.async_database_url.startswith("sqlite") else {}

# pool_pre_ping: tests each connection with a lightweight ping before
# handing it out. Without this, a connection that Render's Postgres
# silently dropped after sitting idle causes the NEXT request that
# happens to draw it from the pool to fail outright — a real, common
# production failure mode, not a hypothetical one.
#
# pool_recycle: proactively discards and reopens connections older
# than this, before they have a chance to go stale in the first
# place. 1800s (30 min) sits safely under typical managed-Postgres
# idle-connection timeouts.
#
# Both are no-ops for SQLite (which ignores pooling args), so this is
# safe to apply unconditionally rather than branching on database type.
engine = create_async_engine(
    settings.async_database_url,
    connect_args=connect_args,
    pool_pre_ping=True,
    pool_recycle=1800,
)

SessionLocal = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with SessionLocal() as db:
        yield db


# ---------------------------------------------------------------------------
# Second, entirely separate database (Sanchaydb) for the
# server-authoritative accounts/transactions/budgets work. Everything
# above this line is untouched and still points at neondb -- every
# existing route (auth, sync, shared expenses, health, legal) keeps
# working against the exact data it works against today.
#
# A genuinely separate DeclarativeBase, not shared metadata with Base
# above: these are two physically different databases with no possible
# foreign keys between them, so there's no reason for their table
# metadata to be entangled, and real reasons for it not to be (Alembic
# autogenerate diffing one database's models against a mixed-metadata
# picture that includes tables that will never exist there).
# ---------------------------------------------------------------------------

sanchay_app_connect_args = (
    {"check_same_thread": False} if settings.sanchay_app_async_database_url.startswith("sqlite") else {}
)

sanchay_app_engine = create_async_engine(
    settings.sanchay_app_async_database_url,
    connect_args=sanchay_app_connect_args,
    pool_pre_ping=True,
    pool_recycle=1800,
)

SanchayAppSessionLocal = async_sessionmaker(bind=sanchay_app_engine, class_=AsyncSession, expire_on_commit=False)


class SanchayAppBase(DeclarativeBase):
    pass


async def get_sanchay_app_db() -> AsyncGenerator[AsyncSession, None]:
    async with SanchayAppSessionLocal() as db:
        yield db
