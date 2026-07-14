from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.core.config import settings
from app.core.database import Base
from app.models import user  # noqa: F401 — registers models with Base.metadata

config = context.config
config.set_main_option("sqlalchemy.url", settings.database_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=settings.database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        # render_as_batch: SQLite (used for local dev/testing per this
        # repo's README) can't ALTER a table to add a foreign key
        # constraint directly -- it needs "batch mode" (copy-and-move
        # via a temp table). This was never hit until the migration
        # that added Settlement.group_id, the first one in this repo's
        # history to add a FK to an existing table rather than create
        # a new one outright. Safe to leave on unconditionally: it's a
        # transparent no-op for PostgreSQL (production), which
        # supports direct ALTER natively.
        context.configure(connection=connection, target_metadata=target_metadata, render_as_batch=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
