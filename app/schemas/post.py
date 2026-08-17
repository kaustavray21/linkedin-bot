from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class PostCreate(BaseModel):
    content: str = Field(..., min_length=1, max_length=3000)
    image_url: str | None = None
    # How the image got here: ai | upload | url. media_service has always
    # returned this; nothing carried it as far as the post until now.
    image_source: str | None = None
    scheduled_time: datetime | None = None


class PostUpdate(BaseModel):
    """Every field is optional, and omitted is not the same as null.

    The route reads `model_dump(exclude_unset=True)`, so a field left out is
    untouched while an explicit `null` clears it. Without that distinction there
    is no way to remove an image or un-schedule a post — the old
    `if value is not None` check treated both cases as "leave it alone".
    """

    content: str | None = Field(None, min_length=1, max_length=3000)
    image_url: str | None = None
    image_source: str | None = None
    scheduled_time: datetime | None = None


class PostResponse(BaseModel):
    id: int
    user_id: int
    content: str
    image_url: str | None
    image_source: str | None = None
    status: str
    linkedin_post_id: str | None
    scheduled_time: datetime | None
    published_time: datetime | None
    created_at: datetime
    updated_at: datetime


class PostPublishResponse(BaseModel):
    message: str
    linkedin_post_id: str
