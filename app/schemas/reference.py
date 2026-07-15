from __future__ import annotations

from pydantic import BaseModel


class ReferenceProfileSummary(BaseModel):
    slug: str
    profile_url: str | None
    post_count: int


class StyleProfileResponse(BaseModel):
    slug: str
    sample_count: int
    avg_word_count: float
    avg_line_count: float
    avg_hashtag_count: float
    common_hashtags: list[str]
    emoji_frequency: str
    hook_style: str
    line_rhythm: str
    has_cta_pattern: bool


class ReferencePostInfo(BaseModel):
    id: str
    slug: str
    snippet: str
    full_text: str

