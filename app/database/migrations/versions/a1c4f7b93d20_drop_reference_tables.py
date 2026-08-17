"""drop reference tables

Generation now takes its exemplar from a discovered post, so the local
reference-file subsystem and the two tables it synced into are gone.

Downgrade recreates the tables but not their contents. That is not a loss: the
rows were only ever a mirror of `app/references/*.txt` on disk, rebuilt on every
startup by the loader that has also been removed. There was never any data here
that did not come from a file.

Revision ID: a1c4f7b93d20
Revises: e25708ce582b
Create Date: 2026-08-17
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a1c4f7b93d20"
down_revision: str | Sequence[str] | None = "e25708ce582b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Child first — reference_posts carries the FK.
    op.drop_table("reference_posts")
    op.drop_table("reference_profiles")


def downgrade() -> None:
    op.create_table(
        "reference_profiles",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("slug", sa.String(length=100), nullable=False),
        sa.Column("profile_url", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("slug"),
    )
    op.create_table(
        "reference_posts",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("profile_id", sa.Integer(), nullable=False),
        sa.Column("filename", sa.String(length=100), nullable=False),
        sa.Column("full_text", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=True),
        sa.ForeignKeyConstraint(["profile_id"], ["reference_profiles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
