"""record which type a discovered post was classified into

The slug is stored rather than a foreign key. Types get merged, and a post
already classified into the losing type still has to resolve to something — a
merge repoints these rows, which is simpler than teaching a constraint about it.

NULL means "not classified", which is a normal outcome rather than an error: the
classifier refuses whenever the model is unavailable, returns placeholder copy,
or proposes a new type without justifying it. Existing rows stay NULL; they were
stored before there was a taxonomy to classify them into.

Revision ID: e6b1c9a4d772
Revises: d4f8e2a71b93
Create Date: 2026-08-18
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e6b1c9a4d772"
down_revision: str | Sequence[str] | None = "d4f8e2a71b93"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "discovered_posts",
        sa.Column("post_type_slug", sa.String(length=50), nullable=True),
    )
    op.create_index(
        "ix_discovered_posts_post_type_slug", "discovered_posts", ["post_type_slug"]
    )


def downgrade() -> None:
    op.drop_index("ix_discovered_posts_post_type_slug", table_name="discovered_posts")
    op.drop_column("discovered_posts", "post_type_slug")
