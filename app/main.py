from contextlib import asynccontextmanager

from fastapi import FastAPI, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from sqlalchemy import text

from app.core.config import settings
from app.core.database import Base, engine
from app.core.error_handlers import unhandled_exception_handler, validation_exception_handler
from app.core.limiter import limiter
from app.core.logging import configure_logging
from app.models import user  # noqa: F401 — registers the model with Base.metadata
from app.routers import auth, shared_expenses, sync

configure_logging()


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
    yield


app = FastAPI(
    title="Sanchay API",
    description="Identity and auth service for Sanchay. Does not store financial data.",
    version="1.22.0",
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
    A REAL readiness check, not a static 200 — verifies the database
    is actually reachable, since "the process is running but can't
    reach its database" is the most common way a service silently
    degrades in production while still looking "up" to a naive check.

    Returns a genuine 503 on failure, not a 200 with a "degraded"
    string buried in the body — an uptime monitor or load balancer
    only acts on the status code, not the payload.
    """
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return {"status": "ok", "database": "ok"}
    except Exception:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {"status": "degraded", "database": "unreachable"}
