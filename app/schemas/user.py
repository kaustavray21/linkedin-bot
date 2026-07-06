from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class UserResponse(BaseModel):
    id: int
    linkedin_member_id: str
    full_name: str | None
    email: str | None
    profile_picture: str | None
    created_at: datetime
    updated_at: datetime
