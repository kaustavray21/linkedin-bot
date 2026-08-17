"""
app/services/style_service.py

Extracts a structural StyleProfile from a set of reference posts.

Deliberately captures *pattern*, not content: hook style, line rhythm,
hashtag habits, emoji frequency, CTA pattern. No sentence from any
reference post should ever appear verbatim in generated output — this
module never returns raw post text, only aggregate structural signals.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

HASHTAG_RE = re.compile(r"#\w+")
EMOJI_RE = re.compile(
    "[\U0001F300-\U0001FAFF\U00002700-\U000027BF\U0001F900-\U0001F9FF]"
)

CTA_MARKERS = (
    "comment", "let me know", "what do you think",
    "share your", "drop a", "thoughts?",
)


@dataclass
class StyleProfile:
    sample_count: int
    avg_word_count: float
    avg_line_count: float
    avg_hashtag_count: float
    common_hashtags: list[str]
    emoji_frequency: str   # "none" | "light" | "heavy"
    hook_style: str        # "question" | "bold_statement" | "stat_or_number" | "story_open"
    line_rhythm: str       # "short_punchy" | "flowing_paragraphs"
    has_cta_pattern: bool


def _classify_hook(first_line: str) -> str:
    line = first_line.strip()
    if line.endswith("?"):
        return "question"
    if re.match(r"^\d", line):
        return "stat_or_number"
    if len(line.split()) <= 8:
        return "bold_statement"
    return "story_open"


def _classify_emoji_frequency(texts: list[str]) -> str:
    total_emoji = sum(len(EMOJI_RE.findall(t)) for t in texts)
    per_post = total_emoji / max(len(texts), 1)
    if per_post < 0.5:
        return "none"
    if per_post < 3:
        return "light"
    return "heavy"


def _has_cta(text: str) -> bool:
    tail = " ".join(text.strip().splitlines()[-2:]).lower()
    return any(marker in tail for marker in CTA_MARKERS)


def extract_style_profile(posts: list[str]) -> StyleProfile:
    """
    posts: raw text of the exemplar post(s) being profiled. Pass a
    single profile's posts for a per-creator style, or the combined
    list from load_all_posts() for a blended style.
    """
    if not posts:
        raise ValueError("Need at least one reference post to build a StyleProfile")

    word_counts, line_counts, hashtag_counts = [], [], []
    hashtag_pool: Counter[str] = Counter()
    hooks, cta_hits = [], []

    for text in posts:
        words = text.split()
        lines = [l for l in text.splitlines() if l.strip()]
        tags = HASHTAG_RE.findall(text)

        word_counts.append(len(words))
        line_counts.append(len(lines))
        hashtag_counts.append(len(tags))
        hashtag_pool.update(tags)

        if lines:
            hooks.append(_classify_hook(lines[0]))
        cta_hits.append(_has_cta(text))

    avg_words = sum(word_counts) / len(posts)
    avg_lines = sum(line_counts) / len(posts)

    return StyleProfile(
        sample_count=len(posts),
        avg_word_count=round(avg_words, 1),
        avg_line_count=round(avg_lines, 1),
        avg_hashtag_count=round(sum(hashtag_counts) / len(posts), 1),
        common_hashtags=[tag for tag, _ in hashtag_pool.most_common(8)],
        emoji_frequency=_classify_emoji_frequency(posts),
        hook_style=Counter(hooks).most_common(1)[0][0] if hooks else "story_open",
        line_rhythm="short_punchy" if avg_words / max(avg_lines, 1) < 12 else "flowing_paragraphs",
        has_cta_pattern=sum(cta_hits) >= len(posts) / 2,
    )
