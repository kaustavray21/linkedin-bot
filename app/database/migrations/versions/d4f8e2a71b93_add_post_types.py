"""add the post_types taxonomy and seed it

The taxonomy extends itself at runtime — the classifier registers a type it has
coined without asking — so the table has to record provenance from the first
row. `origin` separates the six seeded here from anything a model invents, and
`why_new` keeps the reason it gave.

`usage_count` / `last_used_at` exist for the merge pass rather than for display:
an unconstrained taxonomy accumulates near-synonyms, and the way to find them
later is to look for coinages used once and never again.

The seed rows go in as part of creating the table, so they exist from the moment
the classifier can read it — there is no window where a classification runs
against an empty taxonomy and coins six types that should have been there. The
downgrade drops the table outright, which is what makes the pair reversible.

Revision ID: d4f8e2a71b93
Revises: c93a1f60d4e7
Create Date: 2026-08-18
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d4f8e2a71b93"
down_revision: str | Sequence[str] | None = "c93a1f60d4e7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


SEED_TYPES = [
    ("story", "Story", "A personal narrative with a turn — something happened, and it changed how the author thinks."),
    ("contrarian", "Contrarian take", "Argues against a position the author's audience is assumed to hold."),
    ("listicle", "List", "Enumerated advice or observations, structured as a countable set."),
    ("case_study", "Case study", "Walks through a specific situation and its measurable outcome."),
    ("announcement", "Announcement", "Shares news: a launch, a role, a milestone, a result."),
    ("question", "Question", "Opens with a question to the reader and invites replies rather than asserting."),
]


def upgrade() -> None:
    op.create_table(
        "post_types",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("slug", sa.String(length=50), nullable=False),
        sa.Column("label", sa.String(length=100), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("origin", sa.String(length=10), server_default="ai", nullable=False),
        sa.Column("why_new", sa.Text(), nullable=True),
        sa.Column("first_seen_at", sa.DateTime(), server_default=sa.func.now(), nullable=True),
        sa.Column("last_used_at", sa.DateTime(), nullable=True),
        sa.Column("usage_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("merged_into_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["merged_into_id"], ["post_types.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_post_types_slug", "post_types", ["slug"], unique=True)

    post_types = sa.table(
        "post_types",
        sa.column("slug", sa.String),
        sa.column("label", sa.String),
        sa.column("description", sa.Text),
        sa.column("origin", sa.String),
    )
    op.bulk_insert(
        post_types,
        [
            {"slug": slug, "label": label, "description": description, "origin": "seed"}
            for slug, label, description in SEED_TYPES
        ],
    )


def downgrade() -> None:
    op.drop_index("ix_post_types_slug", table_name="post_types")
    op.drop_table("post_types")
