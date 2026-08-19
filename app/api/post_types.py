"""
app/api/post_types.py

The taxonomy, and the merge pass that keeps it usable.

Types register themselves during discovery without asking. Merging them back
together is the opposite: it repoints every post classified into the losing
type, so it only ever happens when someone asks for it. This endpoint proposes;
it does not act on its own.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.connection import get_session
from app.database.models import PostType
from app.services.post_type_service import merge_proposals, merge_types

router = APIRouter(prefix="/post-types", tags=["post-types"])


class PostTypeResponse(BaseModel):
    slug: str
    label: str
    description: str | None
    origin: str
    why_new: str | None
    usage_count: int
    first_seen_at: datetime | None
    last_used_at: datetime | None


class MergeProposalResponse(BaseModel):
    loser_slug: str
    winner_slug: str | None
    reason: str
    similarity: float | None
    loser_usage: int
    winner_usage: int
    loser_origin: str


class MergeRequest(BaseModel):
    loser_slug: str
    # Omitted or null retires the type instead of folding it into another.
    winner_slug: str | None = None


class MergeResponse(BaseModel):
    merged_into: str | None
    detail: str


@router.get("", response_model=list[PostTypeResponse])
async def list_post_types(db: AsyncSession = Depends(get_session)):
    """Active types, most-used first. Merged and retired types are not listed."""
    rows = (
        await db.execute(
            select(PostType)
            .where(PostType.active.is_(True))
            .order_by(PostType.usage_count.desc(), PostType.slug)
        )
    ).scalars().all()

    return [
        PostTypeResponse(
            slug=t.slug,
            label=t.label,
            description=t.description,
            origin=t.origin,
            why_new=t.why_new,
            usage_count=t.usage_count or 0,
            first_seen_at=t.first_seen_at,
            last_used_at=t.last_used_at,
        )
        for t in rows
    ]


@router.get("/merge-proposals", response_model=list[MergeProposalResponse])
async def list_merge_proposals(db: AsyncSession = Depends(get_session)):
    """What the taxonomy would look like tidied up. Read-only."""
    return [MergeProposalResponse(**p.__dict__) for p in await merge_proposals(db)]


@router.post("/merge", response_model=MergeResponse)
async def merge(body: MergeRequest, db: AsyncSession = Depends(get_session)):
    try:
        winner = await merge_types(db, body.loser_slug, body.winner_slug)
    except ValueError as exc:
        # Naming a type that does not exist is a caller mistake, not a fault.
        raise HTTPException(status_code=400, detail=str(exc))

    await db.commit()
    return MergeResponse(
        merged_into=winner or None,
        detail=(
            f"'{body.loser_slug}' folded into '{winner}'" if winner
            else f"'{body.loser_slug}' retired"
        ),
    )
