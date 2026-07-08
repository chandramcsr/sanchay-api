from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

from app.core.config import settings
from app.core.database import Base, engine
from app.core.error_handlers import validation_exception_handler
from app.core.limiter import limiter
from app.models import user  # noqa: F401 — registers the model with Base.metadata
from app.routers import auth, sync

# Dev/SQLite convenience only: creates tables if they don't exist.
# Postgres in real deployments is migrated with Alembic (see alembic/),
# not this — this line is a no-op once Alembic has run.
Base.metadata.create_all(bind=engine)

app = FastAPI(
    title="Sanchay API",
    description="Identity and auth service for Sanchay. Does not store financial data.",
    version="0.6.0",
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_exception_handler(RequestValidationError, validation_exception_handler)
app.add_middleware(SlowAPIMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(sync.router)


@app.get("/")
def root() -> dict[str, str]:
    """
    Not a duplicate of /health — this exists so Render's own uptime
    ping (a HEAD/GET to '/') gets a real 200 instead of a 404, and so
    anyone who opens the bare URL in a browser sees something useful
    instead of "Not Found".
    """
    return {"service": "sanchay-api", "status": "ok", "docs": "/docs"}


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
