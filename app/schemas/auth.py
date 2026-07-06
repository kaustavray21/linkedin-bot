from __future__ import annotations

from pydantic import BaseModel


class AuthUrlResponse(BaseModel):
    auth_url: str


class CallbackResponse(BaseModel):
    message: str
    user_id: int


class MeResponse(BaseModel):
    id: int
    linkedin_member_id: str
    full_name: str | None
    email: str | None
    profile_picture: str | None
