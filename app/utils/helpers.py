from __future__ import annotations

import uuid
from datetime import datetime


def generate_request_id() -> str:
    return uuid.uuid4().hex[:16]


def parse_datetime(value: str | datetime | None) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        from datetime import datetime as dt
        try:
            return dt.fromisoformat(value)
        except ValueError:
            return None
    return None
