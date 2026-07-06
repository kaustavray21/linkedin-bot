from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class PostCreate(BaseModel):
    content: str = Field(..., min_length=1, max_length=3000)
    image_url: str | None = None
    scheduled_time: datetime | None = None


class PostUpdate(BaseModel):
    content: str | None = Field(None, min_length=1, max_length=3000)
    image_url: str | None = None
    scheduled_time: datetime | None = None


class PostResponse(BaseModel):
    id: int
    user_id: int
    content: str
    image_url: str | None
    status: str
    linkedin_post_id: str | None
    scheduled_time: datetime | None
    published_time: datetime | None
    created_at: datetime
    updated_at: datetime


class PostPublishResponse(BaseModel):
    message: str
    linkedin_post_id: str
