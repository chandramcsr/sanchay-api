from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.core.database import Base, engine
from app.models import user  # noqa: F401 — registers the model with Base.metadata
from app.routers import auth

# Dev/SQLite convenience only: creates tables if they don't exist.
# Postgres in real deployments is migrated with Alembic (see alembic/),
# not this — this line is a no-op once Alembic has run.
Base.metadata.create_all(bind=engine)

app = FastAPI(
    title="Sanchay API",
    description="Identity and auth service for Sanchay. Does not store financial data.",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
