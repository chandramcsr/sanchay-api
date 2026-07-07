"""require display_name on users

Revision ID: 0a59dc91e9dc
Revises: 4a9def23ccd9
Create Date: 2026-07-07 21:36:06.192518

"""
from alembic import op
import sqlalchemy as sa


revision = '0a59dc91e9dc'
down_revision = '4a9def23ccd9'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Note: this fails if any existing row has display_name = NULL.
    # That's intentional — better to fail loudly and backfill than to
    # silently leave a NOT NULL constraint unenforced. No real users
    # exist yet at the time this was written, so it's a non-issue here.
    #
    # batch_alter_table is required for SQLite, which doesn't support
    # ALTER COLUMN directly (Alembic recreates the table under the
    # hood instead). Postgres supports the plain form, but batch mode
    # works transparently on both, so it's used unconditionally rather
    # than branching per-dialect.
    with op.batch_alter_table("users") as batch_op:
        batch_op.alter_column(
            "display_name",
            existing_type=sa.VARCHAR(length=100),
            nullable=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("users") as batch_op:
        batch_op.alter_column(
            "display_name",
            existing_type=sa.VARCHAR(length=100),
            nullable=True,
        )
