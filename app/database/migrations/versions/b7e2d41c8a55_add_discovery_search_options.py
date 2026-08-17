"""add hashtags and timelimit to discovery jobs

A discovery run is queued by the request and executed by a background task that
re-reads the job row, so any search option not stored here is lost between the
two. Both are nullable — an existing job simply searched without them.

Revision ID: b7e2d41c8a55
Revises: a1c4f7b93d20
Create Date: 2026-08-17
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b7e2d41c8a55"
down_revision: str | Sequence[str] | None = "a1c4f7b93d20"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("discovery_jobs", sa.Column("hashtags", sa.JSON(), nullable=True))
    op.add_column("discovery_jobs", sa.Column("timelimit", sa.String(length=10), nullable=True))


def downgrade() -> None:
    op.drop_column("discovery_jobs", "timelimit")
    op.drop_column("discovery_jobs", "hashtags")
