"""
app/services/reference_loader.py

Loads locally-curated LinkedIn reference posts from disk.

Expected layout:
    app/references/
        <profile-slug>/
            linkedin_id.json   # e.g. {"profile_url": "...", "id": "..."}
            ref-1.txt
            ref-2.txt
            ...

No network access, no scraping — this only reads files you've already
saved locally yourself. Nothing here writes back to app/references/;
adding or removing reference posts stays a manual filesystem action,
outside the app.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from sqlalchemy.ext.asyncio import AsyncSession

REFERENCES_DIR = Path(__file__).resolve().parents[1] / "references"


@dataclass
class ReferenceProfile:
    slug: str                                        # folder name, e.g. "sub1"
    metadata: dict = field(default_factory=dict)      # contents of linkedin_id.json
    posts: list[str] = field(default_factory=list)    # raw text of each ref-N.txt

    @property
    def profile_url(self) -> str | None:
        return self.metadata.get("profile_url") or self.metadata.get("url")

    @property
    def post_count(self) -> int:
        return len(self.posts)


def _load_metadata(profile_dir: Path) -> dict:
    # tolerates the "linkind_id.json" typo seen in sub1 as well as the
    # correctly-spelled "linkedin_id.json" used in sub2
    for filename in ("linkedin_id.json", "linkind_id.json"):
        meta_path = profile_dir / filename
        if meta_path.exists():
            try:
                return json.loads(meta_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                return {}
    return {}


def _load_posts(profile_dir: Path) -> list[str]:
    posts = []
    for txt_file in sorted(profile_dir.glob("ref-*.txt")):
        text = txt_file.read_text(encoding="utf-8").strip()
        if text:
            posts.append(text)
    return posts


def load_reference_profiles(base_dir: Path = REFERENCES_DIR) -> list[ReferenceProfile]:
    """Scans app/references/*/ and loads each profile's metadata + post texts."""
    if not base_dir.exists():
        return []

    profiles = []
    for profile_dir in sorted(p for p in base_dir.iterdir() if p.is_dir()):
        metadata = _load_metadata(profile_dir)
        posts = _load_posts(profile_dir)
        if posts:
            profiles.append(ReferenceProfile(slug=profile_dir.name, metadata=metadata, posts=posts))
    return profiles


def load_all_posts(base_dir: Path = REFERENCES_DIR) -> list[str]:
    """Flat list of every reference post text across all profiles — for a blended style."""
    return [post for profile in load_reference_profiles(base_dir) for post in profile.posts]


def get_profile(slug: str, base_dir: Path = REFERENCES_DIR) -> ReferenceProfile | None:
    """Fetch a single profile by folder slug, e.g. 'sub1'."""
    for profile in load_reference_profiles(base_dir):
        if profile.slug == slug:
            return profile
    return None


async def sync_references_to_db(db_session: AsyncSession) -> None:
    """Reads profiles and posts from disk and synchronizes them into the database."""
    from sqlalchemy import select
    from app.database.models import ReferenceProfile, ReferencePost

    profiles = load_reference_profiles()
    for p in profiles:
        # Check if profile already exists in DB
        result = await db_session.execute(
            select(ReferenceProfile).where(ReferenceProfile.slug == p.slug)
        )
        db_profile = result.scalar_one_or_none()
        if not db_profile:
            db_profile = ReferenceProfile(slug=p.slug, profile_url=p.profile_url)
            db_session.add(db_profile)
            await db_session.flush()
        else:
            db_profile.profile_url = p.profile_url

        # Synchronize all ref-*.txt files in the profile dir
        p_dir = REFERENCES_DIR / p.slug
        if p_dir.exists() and p_dir.is_dir():
            for txt_file in sorted(p_dir.glob("ref-*.txt")):
                filename = txt_file.name
                text = txt_file.read_text(encoding="utf-8").strip()
                if not text:
                    continue

                # Check if this post is already stored in DB
                post_res = await db_session.execute(
                    select(ReferencePost)
                    .where(ReferencePost.profile_id == db_profile.id)
                    .where(ReferencePost.filename == filename)
                )
                db_post = post_res.scalar_one_or_none()
                if not db_post:
                    db_post = ReferencePost(
                        profile_id=db_profile.id,
                        filename=filename,
                        full_text=text
                    )
                    db_session.add(db_post)
                else:
                    db_post.full_text = text

