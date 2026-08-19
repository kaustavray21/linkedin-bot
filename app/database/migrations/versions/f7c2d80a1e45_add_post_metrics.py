"""record how published posts perform over time

A time series rather than a snapshot. A single current number cannot tell a post
that died in six hours from one still climbing on day seven, and that difference
is most of the signal the feedback loop is meant to learn from.

`age_hours` is stored rather than derived at read time so readings stay
comparable across posts published at different times of day.

Reactions and comments are read from each post's own public page, through the
same fetcher and parser Discovery uses — no extra LinkedIn scope is involved.
Impressions and reposts need the member analytics API, so they stay NULL until
`r_member_postAnalytics` is granted. NULL means "not measured", never zero.

Revision ID: f7c2d80a1e45
Revises: e6b1c9a4d772
Create Date: 2026-08-19
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f7c2d80a1e45"
down_revision: str | Sequence[str] | None = "e6b1c9a4d772"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "post_metrics",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("post_id", sa.Integer(), nullable=False),
        sa.Column("captured_at", sa.DateTime(), server_default=sa.func.now(), nullable=True),
        sa.Column("age_hours", sa.Integer(), nullable=True),
        sa.Column("reactions", sa.Integer(), nullable=True),
        sa.Column("comments", sa.Integer(), nullable=True),
        sa.Column("impressions", sa.Integer(), nullable=True),
        sa.Column("reposts", sa.Integer(), nullable=True),
        sa.Column("source", sa.String(length=20), server_default="public_page", nullable=False),
        # CASCADE, unlike the lineage tables: a reading is meaningless without
        # the post it measured, and there is nothing to denormalise onto it.
        sa.ForeignKeyConstraint(["post_id"], ["posts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_post_metrics_post_id", "post_metrics", ["post_id"])
    op.create_index("ix_post_metrics_captured_at", "post_metrics", ["captured_at"])


def downgrade() -> None:
    # Just the table. Dropping its indexes first looks tidier and fails:
    # ix_post_metrics_post_id backs the foreign key, and MySQL refuses to drop
    # an index a constraint depends on. Worse, MySQL DDL is not transactional,
    # so the half-completed downgrade left the table with one index missing and
    # the version stamp unchanged — a state no rerun could fix.
    op.drop_table("post_metrics")
