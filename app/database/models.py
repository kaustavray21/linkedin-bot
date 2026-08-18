from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    linkedin_member_id: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    full_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    profile_picture: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    oauth_tokens: Mapped[list[OAuthToken]] = relationship(back_populates="user", cascade="all, delete-orphan")
    posts: Mapped[list[Post]] = relationship(back_populates="user", cascade="all, delete-orphan")
    schedules: Mapped[list[Schedule]] = relationship(back_populates="user", cascade="all, delete-orphan")


class OAuthToken(Base):
    __tablename__ = "oauth_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    access_token: Mapped[str] = mapped_column(Text, nullable=False)
    refresh_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    scope: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    user: Mapped[User] = relationship(back_populates="oauth_tokens")


class Post(Base):
    __tablename__ = "posts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    image_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Provenance only — image_url semantics are unchanged.
    image_source: Mapped[str | None] = mapped_column(String(20), nullable=True)
    status: Mapped[str] = mapped_column(String(50), default="draft", nullable=False)
    linkedin_post_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    scheduled_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    published_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


    user: Mapped[User] = relationship(back_populates="posts")


class Schedule(Base):
    __tablename__ = "schedules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    cron_expression: Mapped[str] = mapped_column(String(100), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    next_run: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    user: Mapped[User] = relationship(back_populates="schedules")


class ApiLog(Base):
    __tablename__ = "api_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"), nullable=True)
    endpoint: Mapped[str] = mapped_column(String(500), nullable=False)
    request: Mapped[str | None] = mapped_column(Text, nullable=True)
    response: Mapped[str | None] = mapped_column(Text, nullable=True)
    status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class DiscoveredPost(Base):
    """A public LinkedIn post found by the discovery pipeline.

    All datetimes are naive UTC — the driver drops tzinfo on write, so storing
    aware values here would silently produce a mix of aware and naive rows.
    Follow the existing convention in post_service.publish_post().
    """

    __tablename__ = "discovered_posts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"), nullable=True)

    keyword: Mapped[str] = mapped_column(String(255), index=True, nullable=False)
    source: Mapped[str] = mapped_column(String(50), nullable=False)
    post_url: Mapped[str] = mapped_column(String(500), unique=True, nullable=False)

    author_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    author_headline: Mapped[str | None] = mapped_column(String(500), nullable=True)
    author_profile_url: Mapped[str | None] = mapped_column(String(500), nullable=True)

    content_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    snippet: Mapped[str | None] = mapped_column(Text, nullable=True)
    hashtags: Mapped[list | None] = mapped_column(JSON, nullable=True)
    image_url: Mapped[str | None] = mapped_column(String(500), nullable=True)

    posted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # Nullable on purpose: None means "could not read", which must never be
    # conflated with a genuine zero when ranking.
    reactions: Mapped[int | None] = mapped_column(Integer, nullable=True)
    comments: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reposts: Mapped[int | None] = mapped_column(Integer, nullable=True)
    metrics_source: Mapped[str] = mapped_column(String(20), default="inferred", nullable=False)

    serp_rank: Mapped[int | None] = mapped_column(Integer, nullable=True)
    query_overlap: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    engagement_score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)

    # Survives purge — anonymous structure, none of the source's wording.
    layout_skeleton: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    raw_payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    fetched_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    purged_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    used_as_reference: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class DiscoveryJob(Base):
    __tablename__ = "discovery_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"), nullable=True)

    keyword: Mapped[str] = mapped_column(String(255), nullable=False)
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="queued", nullable=False)

    # Persisted because the run happens in a background task that re-reads this
    # row — anything not stored here is lost between queueing and executing.
    hashtags: Mapped[list | None] = mapped_column(JSON, nullable=True)
    timelimit: Mapped[str | None] = mapped_column(String(10), nullable=True)  # d|w|m|y

    requested_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    found_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    fetched_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    parse_failures: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)



class DraftLineage(Base):
    """Which discovered post a draft was cloned from — and enough of it to
    survive that post being deleted.

    Everything from `exemplar_url` down is a COPY taken at generation time, not
    a join. Discovered posts are hard-deleted at 30 days; a lineage row that
    only held a foreign key would go blank at exactly the moment the history is
    worth having. The layout skeleton is copied for the same reason it survived
    a purge before — it carries no wording from the source, and it is what keeps
    an already-generated draft reproducible.
    """

    __tablename__ = "draft_lineage"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # Both nullable with ON DELETE SET NULL: either side can go away, and the
    # snapshot below is what the history actually renders from.
    post_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("posts.id", ondelete="SET NULL"), nullable=True, index=True
    )
    discovered_post_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("discovered_posts.id", ondelete="SET NULL"), nullable=True
    )

    exemplar_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    exemplar_author: Mapped[str | None] = mapped_column(String(255), nullable=True)
    exemplar_snippet: Mapped[str | None] = mapped_column(Text, nullable=True)
    exemplar_reactions: Mapped[int | None] = mapped_column(Integer, nullable=True)
    exemplar_comments: Mapped[int | None] = mapped_column(Integer, nullable=True)
    exemplar_captured_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    exemplar_skeleton: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    params_used: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    used_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class PostType(Base):
    """A kind of post — `story`, `contrarian`, and whatever the model coins next.

    The taxonomy extends itself: when a discovered post fits none of the existing
    types, the classifier registers a new one without asking. `origin` and
    `why_new` are what keep that honest — every row records whether a human
    seeded it or a model invented it, and in the latter case the reason given at
    the time. Auto-add without permission, never without a record.

    `usage_count` and `last_used_at` are not decoration. An unconstrained
    taxonomy drifts into `storytelling`, `personal_story` and `narrative` as
    three separate types within a week, at which point classification means
    nothing; those two columns are what let a merge pass find the coinages that
    were used once and never again.

    Merged types are deactivated rather than deleted, with `merged_into_id`
    pointing at the survivor: posts already classified into the loser still need
    somewhere to resolve.
    """

    __tablename__ = "post_types"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    slug: Mapped[str] = mapped_column(String(50), unique=True, index=True, nullable=False)
    label: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    origin: Mapped[str] = mapped_column(String(10), default="ai", nullable=False)  # seed | ai
    why_new: Mapped[str | None] = mapped_column(Text, nullable=True)

    first_seen_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    usage_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    merged_into_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("post_types.id", ondelete="SET NULL"), nullable=True
    )
