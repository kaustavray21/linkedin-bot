from __future__ import annotations

from app.services.scheduler_service import SchedulerService

_scheduler_service: SchedulerService | None = None


def get_scheduler_service() -> SchedulerService:
    global _scheduler_service
    if _scheduler_service is None:
        _scheduler_service = SchedulerService()
    return _scheduler_service


def set_scheduler_service(service: SchedulerService) -> None:
    global _scheduler_service
    _scheduler_service = service
