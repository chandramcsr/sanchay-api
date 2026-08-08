from contextlib import asynccontextmanager

from fastapi import FastAPI, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from sqlalchemy import text

import sentry_sdk

from app.core.config import settings
from app.core.database import Base, SanchayAppBase, engine, sanchay_app_engine
from app.core.error_handlers import unhandled_exception_handler, validation_exception_handler
from app.core.limiter import limiter
from app.core.logging import configure_logging
from app.models import user  # noqa: F401 — registers the model with Base.metadata
from app.models.account import Account  # noqa: F401 — registers with SanchayAppBase.metadata
from app.models.budget import Budget  # noqa: F401
from app.models.discussion import Discussion  # noqa: F401
from app.models.recurring_rule import RecurringRule  # noqa: F401
from app.models.savings_goal import SavingsGoal  # noqa: F401
from app.models.transaction import Transaction  # noqa: F401
from app.routers import accounts, auth, budgets, discussions, feedback, health, legal, recurring_rules, savings_goals, shared_expenses, sync, transactions

configure_logging()

APP_VERSION = "1.45.0"

# dsn=None is a documented no-op in the SDK, not a crash -- so this is
# safe to call unconditionally even in local dev/tests where
# SENTRY_DSN is never set, same as every other optional integration in
# this app. traces_sample_rate=1.0 (capture every transaction, not a
# sample) is deliberate at this traffic level -- sampling matters once
# volume could threaten a paid tier's usage cap, which isn't a real
# concern yet.
sentry_sdk.init(
    dsn=settings.sentry_dsn,
    environment=settings.sentry_environment,
    release=f"sanchay-api@{APP_VERSION}",
    traces_sample_rate=1.0,
    send_default_pii=False,  # no request bodies/headers/user IP by default -- this API handles auth credentials and financial-adjacent data
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Dev/SQLite convenience only: creates tables if they don't exist.
    # Postgres in real deployments is migrated with Alembic (see
    # alembic/), not this — this is a no-op once Alembic has run.
    # run_sync() is needed because create_all() itself is a sync
    # SQLAlchemy call; the async engine can still invoke it inside an
    # async connection context.
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    # Same dev/SQLite convenience, for the second (Sanchaydb) database --
    # a no-op once Alembic has run against the real Postgres deployment,
    # same as above.
    async with sanchay_app_engine.begin() as conn:
        await conn.run_sync(SanchayAppBase.metadata.create_all)
    yield


app = FastAPI(
    title="Sanchay API",
    description=(
        "Identity/auth, shared expenses, health, and legal records for "
        "ledger-app (see that repo for what stays local-only). Also now "
        "the server-authoritative store for accounts/transactions/"
        "budgets (Sanchaydb, a separate database, Clerk-authenticated) "
        "for the Sanchay-app rebuild."
    ),
    version=APP_VERSION,
    lifespan=lifespan,
)

API_V1_PREFIX = "/api/v1"

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_exception_handler(RequestValidationError, validation_exception_handler)
# Catch-all for anything unhandled: without this, a genuine bug produces
# whatever raw error FastAPI's default 500 handling emits — this
# guarantees every unexpected failure logs the real detail server-side
# and returns a safe, generic message to the client instead.
app.add_exception_handler(Exception, unhandled_exception_handler)
app.add_middleware(SlowAPIMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Only the actual business API is versioned (/api/v1/auth/..., /api/v1/sync/...).
# /, /health stay unversioned on purpose — they're ops endpoints (uptime
# pings, load-balancer health checks), not part of the API surface a
# client integrates against, and versioning them would just complicate
# anything that pings them without buying anything real.
app.include_router(auth.router, prefix=API_V1_PREFIX)
app.include_router(sync.router, prefix=API_V1_PREFIX)
app.include_router(shared_expenses.router, prefix=API_V1_PREFIX)
app.include_router(feedback.router, prefix=API_V1_PREFIX)
app.include_router(health.router, prefix=API_V1_PREFIX)
app.include_router(legal.router, prefix=API_V1_PREFIX)
app.include_router(accounts.router, prefix=API_V1_PREFIX)
app.include_router(transactions.router, prefix=API_V1_PREFIX)
app.include_router(budgets.router, prefix=API_V1_PREFIX)
app.include_router(recurring_rules.router, prefix=API_V1_PREFIX)
app.include_router(savings_goals.router, prefix=API_V1_PREFIX)
app.include_router(discussions.router, prefix=API_V1_PREFIX)


@app.get("/")
def root() -> dict[str, str]:
    """
    Not a duplicate of /health — this exists so Render's own uptime
    ping (a HEAD/GET to '/') gets a real 200 instead of a 404, and so
    anyone who opens the bare URL in a browser sees something useful
    instead of "Not Found".
    """
    return {"service": "sanchay-api", "status": "ok", "docs": "/docs", "api": API_V1_PREFIX}


@app.get("/health")
async def health(response: Response) -> dict[str, str]:
    """
    A REAL readiness check, not a static 200 — verifies both databases
    are actually reachable (neondb for every existing route, Sanchaydb
    for accounts/transactions/budgets), since "the process is running
    but can't reach its database" is the most common way a service
    silently degrades in production while still looking "up" to a
    naive check. Checking only the original engine would let a
    Sanchaydb-specific outage report as healthy while every new route
    was actually broken.

    Returns a genuine 503 on failure, not a 200 with a "degraded"
    string buried in the body — an uptime monitor or load balancer
    only acts on the status code, not the payload.
    """
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        async with sanchay_app_engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return {"status": "ok", "database": "ok", "sanchay_app_database": "ok"}
    except Exception:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {"status": "degraded", "database": "unreachable"}
