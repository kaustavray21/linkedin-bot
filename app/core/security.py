from __future__ import annotations

import secrets
from datetime import datetime, timezone



def generate_state() -> str:
    return secrets.token_urlsafe(32)


def validate_state(state: str, stored_state: str) -> bool:
    if not state or not stored_state:
        return False
    return secrets.compare_digest(state, stored_state)


def mask_secret(value: str | None) -> str:
    if not value:
        return ""
    if len(value) <= 8:
        return "***"
    return value[:4] + "****" + value[-4:]


def utcnow() -> datetime:
    return datetime.now(timezone.utc)
