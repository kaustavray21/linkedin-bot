from __future__ import annotations

from app.core.exceptions import ValidationException


def validate_content_length(content: str, max_length: int = 3000) -> None:
    if len(content) > max_length:
        raise ValidationException(f"Content exceeds {max_length} characters")


def validate_cron_expression(expression: str) -> None:
    from apscheduler.triggers.cron import CronTrigger
    try:
        CronTrigger.from_crontab(expression)
    except (ValueError, TypeError) as e:
        raise ValidationException(f"Invalid cron expression: {e}")
