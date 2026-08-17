from __future__ import annotations


from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppException
from app.database.connection import get_session
from app.database.models import Post
from app.schemas.post import PostCreate, PostPublishResponse, PostResponse, PostUpdate
from app.services.post_service import PostService

router = APIRouter(prefix="/posts", tags=["posts"])


def _post_to_response(post: Post) -> PostResponse:
    return PostResponse(
        id=post.id,
        user_id=post.user_id,
        content=post.content,
        image_url=post.image_url,
        image_source=post.image_source,
        status=post.status,
        linkedin_post_id=post.linkedin_post_id,
        scheduled_time=post.scheduled_time,
        published_time=post.published_time,
        created_at=post.created_at,
        updated_at=post.updated_at,
    )


@router.post("/", response_model=PostResponse, status_code=201)
async def create_post(
    body: PostCreate,
    user_id: int = Query(...),
    db: AsyncSession = Depends(get_session),
):
    service = PostService(db)
    try:
        post = await service.create_draft(
            user_id=user_id,
            content=body.content,
            image_url=body.image_url,
            image_source=body.image_source,
            scheduled_time=body.scheduled_time,
        )
        return _post_to_response(post)
    except AppException as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail)


@router.get("/", response_model=list[PostResponse])
async def list_posts(
    user_id: int = Query(...),
    status: str | None = Query(None, description="Comma-separated statuses to include"),
    db: AsyncSession = Depends(get_session),
):
    """List a user's posts, optionally narrowed by status.

    Drafts and published posts now live in different places in the UI — the
    library and History respectively — so each asks for the slice it owns
    rather than fetching everything and filtering twice on the client.
    """
    service = PostService(db)
    posts = await service.get_user_posts(user_id)

    if status:
        wanted = {s.strip() for s in status.split(",") if s.strip()}
        posts = [p for p in posts if p.status in wanted]

    return [_post_to_response(p) for p in posts]


@router.get("/{post_id}", response_model=PostResponse)
async def get_post(
    post_id: int,
    user_id: int = Query(...),
    db: AsyncSession = Depends(get_session),
):
    service = PostService(db)
    try:
        post = await service.get_post(post_id, user_id)
        return _post_to_response(post)
    except AppException as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail)


@router.put("/{post_id}", response_model=PostResponse)
async def update_post(
    post_id: int,
    body: PostUpdate,
    user_id: int = Query(...),
    db: AsyncSession = Depends(get_session),
):
    service = PostService(db)
    try:
        # exclude_unset is what separates "leave this alone" from "clear this".
        # Passing body.image_url directly would collapse both into None again.
        post = await service.update_draft(
            post_id=post_id,
            user_id=user_id,
            **body.model_dump(exclude_unset=True),
        )
        return _post_to_response(post)
    except AppException as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail)


@router.delete("/{post_id}", status_code=204)
async def delete_post(
    post_id: int,
    user_id: int = Query(...),
    db: AsyncSession = Depends(get_session),
):
    service = PostService(db)
    try:
        await service.delete_post(post_id, user_id)
    except AppException as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail)


@router.post("/{post_id}/publish", response_model=PostPublishResponse)
async def publish_post(
    post_id: int,
    user_id: int = Query(...),
    db: AsyncSession = Depends(get_session),
):
    service = PostService(db)
    try:
        result = await service.publish_post(post_id, user_id)
        return PostPublishResponse(
            message="Post published successfully",
            linkedin_post_id=result["linkedin_post_id"],
        )
    except AppException as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail)
