"""add draft lineage and backfill discovery expiry

Two things that have to land together.

`draft_lineage` records which discovered post a draft was cloned from, with a
denormalised copy of that post. The copy is the point: discovered posts are now
hard-deleted at 30 days, and a row holding only a foreign key would go blank at
exactly the moment the history becomes interesting.

The backfill fixes a quieter problem. `expires_at` is computed at INSERT from
`discovery_retention_days`, so changing that setting from 90 to 30 only affected
new rows — every post already stored kept a 90-day expiry, and the shortened
retention would have appeared to do nothing for two months.

Revision ID: c93a1f60d4e7
Revises: b7e2d41c8a55
Create Date: 2026-08-17
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c93a1f60d4e7"
down_revision: str | Sequence[str] | None = "b7e2d41c8a55"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "draft_lineage",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("post_id", sa.Integer(), nullable=True),
        sa.Column("discovered_post_id", sa.Integer(), nullable=True),
        sa.Column("exemplar_url", sa.String(length=500), nullable=True),
        sa.Column("exemplar_author", sa.String(length=255), nullable=True),
        sa.Column("exemplar_snippet", sa.Text(), nullable=True),
        sa.Column("exemplar_reactions", sa.Integer(), nullable=True),
        sa.Column("exemplar_comments", sa.Integer(), nullable=True),
        sa.Column("exemplar_captured_at", sa.DateTime(), nullable=True),
        sa.Column("exemplar_skeleton", sa.JSON(), nullable=True),
        sa.Column("params_used", sa.JSON(), nullable=True),
        sa.Column("used_at", sa.DateTime(), server_default=sa.func.now(), nullable=True),
        sa.ForeignKeyConstraint(["post_id"], ["posts.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["discovered_post_id"], ["discovered_posts.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_draft_lineage_post_id", "draft_lineage", ["post_id"])

    # Re-derive from fetched_at rather than trusting the stored value: the old
    # value encodes the OLD retention window, which is exactly what is wrong.
    op.execute(
        "UPDATE discovered_posts "
        "SET expires_at = DATE_ADD(fetched_at, INTERVAL 30 DAY) "
        "WHERE fetched_at IS NOT NULL"
    )


def downgrade() -> None:
    op.drop_index("ix_draft_lineage_post_id", table_name="draft_lineage")
    op.drop_table("draft_lineage")
    op.execute(
        "UPDATE discovered_posts "
        "SET expires_at = DATE_ADD(fetched_at, INTERVAL 90 DAY) "
        "WHERE fetched_at IS NOT NULL"
    )
