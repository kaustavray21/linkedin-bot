from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_scheduler_service
from app.core.exceptions import AppException
from app.database.connection import get_session
from app.schemas.scheduler import ScheduleCreate, ScheduleResponse
from app.services.scheduler_service import SchedulerService

router = APIRouter(prefix="/scheduler", tags=["scheduler"])


@router.post("/", response_model=ScheduleResponse, status_code=201)
async def create_schedule(
    body: ScheduleCreate,
    user_id: int = Query(...),
    db: AsyncSession = Depends(get_session),
    scheduler: SchedulerService = Depends(get_scheduler_service),
):
    try:
        schedule = await scheduler.create_schedule(
            session=db,
            user_id=user_id,
            cron_expression=body.cron_expression,
        )
        return ScheduleResponse(
            id=schedule.id,
            user_id=schedule.user_id,
            cron_expression=schedule.cron_expression,
            is_active=schedule.is_active,
            next_run=schedule.next_run,
            created_at=schedule.created_at,
        )
    except AppException as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail)


@router.get("/", response_model=list[ScheduleResponse])
async def list_schedules(
    user_id: int = Query(...),
    db: AsyncSession = Depends(get_session),
    scheduler: SchedulerService = Depends(get_scheduler_service),
):
    schedules = await scheduler.get_user_schedules(session=db, user_id=user_id)
    return [
        ScheduleResponse(
            id=s.id,
            user_id=s.user_id,
            cron_expression=s.cron_expression,
            is_active=s.is_active,
            next_run=s.next_run,
            created_at=s.created_at,
        )
        for s in schedules
    ]


@router.delete("/{schedule_id}", status_code=204)
async def delete_schedule(
    schedule_id: int,
    user_id: int = Query(...),
    db: AsyncSession = Depends(get_session),
    scheduler: SchedulerService = Depends(get_scheduler_service),
):
    try:
        await scheduler.delete_schedule(session=db, schedule_id=schedule_id, user_id=user_id)
    except AppException as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail)
