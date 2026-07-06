from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class ScheduleCreate(BaseModel):
    cron_expression: str


class ScheduleResponse(BaseModel):
    id: int
    user_id: int
    cron_expression: str
    is_active: bool
    next_run: datetime | None
    created_at: datetime
