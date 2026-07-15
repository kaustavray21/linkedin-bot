from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from app.database.connection import get_session
from app.database.models import ReferenceProfile, ReferencePost
from app.schemas.reference import ReferenceProfileSummary, StyleProfileResponse, ReferencePostInfo
from app.services.style_service import extract_style_profile

router = APIRouter(prefix="/reference", tags=["reference"])


@router.get("/profiles", response_model=list[ReferenceProfileSummary])
async def list_profiles(db: AsyncSession = Depends(get_session)):
    """List all available reference profiles/creators from the database."""
    try:
        stmt = select(ReferenceProfile)
        result = await db.execute(stmt)
        profiles = result.scalars().all()
        
        res = []
        for p in profiles:
            count_stmt = select(func.count(ReferencePost.id)).where(ReferencePost.profile_id == p.id)
            cnt = (await db.execute(count_stmt)).scalar() or 0
            res.append(
                ReferenceProfileSummary(
                    slug=p.slug,
                    profile_url=p.profile_url,
                    post_count=cnt,
                )
            )
        return res
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/style-profile/{slug}", response_model=StyleProfileResponse)
async def get_style_profile(slug: str, db: AsyncSession = Depends(get_session)):
    """Get the extracted StyleProfile for a single profile or all (combined) from the database."""
    try:
        if slug == "combined":
            stmt = select(ReferencePost.full_text)
            result = await db.execute(stmt)
            posts = result.scalars().all()
        else:
            profile_stmt = select(ReferenceProfile).where(ReferenceProfile.slug == slug)
            profile = (await db.execute(profile_stmt)).scalar_one_or_none()
            if not profile:
                raise HTTPException(status_code=404, detail=f"Reference profile '{slug}' not found.")
            
            posts_stmt = select(ReferencePost.full_text).where(ReferencePost.profile_id == profile.id)
            posts = (await db.execute(posts_stmt)).scalars().all()

        if not posts:
            raise HTTPException(status_code=404, detail=f"No posts found for reference profile '{slug}'.")

        style = extract_style_profile(list(posts))
        return StyleProfileResponse(
            slug=slug,
            sample_count=style.sample_count,
            avg_word_count=style.avg_word_count,
            avg_line_count=style.avg_line_count,
            avg_hashtag_count=style.avg_hashtag_count,
            common_hashtags=style.common_hashtags,
            emoji_frequency=style.emoji_frequency,
            hook_style=style.hook_style,
            line_rhythm=style.line_rhythm,
            has_cta_pattern=style.has_cta_pattern,
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/profile-posts/{slug}", response_model=list[ReferencePostInfo])
async def list_profile_posts(slug: str, db: AsyncSession = Depends(get_session)):
    """List details of all individual posts inside a profile from the database (or all if combined)."""
    try:
        if slug == "combined":
            stmt = select(ReferencePost, ReferenceProfile.slug).join(ReferenceProfile, ReferencePost.profile_id == ReferenceProfile.id).order_by(ReferenceProfile.slug, ReferencePost.filename)
            result = await db.execute(stmt)
            rows = result.all()
            res = []
            for row in rows:
                post, p_slug = row[0], row[1]
                snippet = post.full_text[:60] + "..." if len(post.full_text) > 60 else post.full_text
                res.append(
                    ReferencePostInfo(
                        id=f"{p_slug}/{post.filename}",
                        slug=p_slug,
                        snippet=snippet,
                        full_text=post.full_text,
                    )
                )
            return res
            
        profile_stmt = select(ReferenceProfile).where(ReferenceProfile.slug == slug)
        profile = (await db.execute(profile_stmt)).scalar_one_or_none()
        if not profile:
            raise HTTPException(status_code=404, detail=f"Reference profile '{slug}' not found.")
            
        posts_stmt = select(ReferencePost).where(ReferencePost.profile_id == profile.id).order_by(ReferencePost.filename)
        posts = (await db.execute(posts_stmt)).scalars().all()
        
        res = []
        for post in posts:
            snippet = post.full_text[:60] + "..." if len(post.full_text) > 60 else post.full_text
            res.append(
                ReferencePostInfo(
                    id=f"{slug}/{post.filename}",
                    slug=slug,
                    snippet=snippet,
                    full_text=post.full_text,
                )
            )
        return res
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
