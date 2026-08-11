"""
app/services/content_generation_service.py

Generates original post content that reproduces the *shape* of a chosen exemplar.

History worth knowing before editing: this module used to pass only aggregate
numbers (avg word count, avg line count) into the prompt, while simultaneously
instructing the model to "match the paragraphing of the reference posts" — posts
that were never actually included in the prompt. The model had nothing to match
against, so it fell back on its own default cadence, which is the uniform
mid-length-paragraph texture that reads as machine-written.

The fix has three parts, all load-bearing:
  1. Show the model one real exemplar, fenced and labelled as structure-only.
  2. Give it an explicit per-block template (layout_service.render_template).
  3. Correct the output deterministically afterwards (layout_service.enforce_layout),
     because models drift toward their default cadence even with a good template.

Step 1 is what makes copying possible, so the similarity gate is not decoration —
it is the counterweight that makes step 1 safe.
"""

from __future__ import annotations

import statistics

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logger import get_logger
from app.database.models import ReferencePost, ReferenceProfile
from app.services.ai_service import AIService, is_template_fallback
from app.services.layout_service import (
    LayoutSkeleton,
    enforce_layout,
    extract_skeleton,
    render_template,
)
from app.services.similarity_service import SimilarityReport, check_similarity
from app.services.style_service import extract_style_profile

log = get_logger()

STYLE_PROMPT_TEMPLATE = """Write an original LinkedIn post about: {topic}

## Structure you must reproduce

{layout_template}

## Structure reference

The post below is provided ONLY so you can absorb its rhythm, pacing and line
breaks. Do not reuse its wording, phrases, stories, names, numbers or claims.
Your post must be about the user's topic, with entirely original content.

<<<STRUCTURE_REFERENCE
{exemplar}
STRUCTURE_REFERENCE

## Voice

- Tone: {tone_hint}
- Opening hook style: {hook_style}
- Closing: {cta_hint}
- Emoji usage: {emoji_frequency}
- Hashtags: about {avg_hashtag_count} tags. Draw inspiration from {common_hashtags},
  but write your own — do not reproduce that list verbatim or in the same order.

## The user's angle

{user_notes}

Return only the post body. No preamble, no explanation, no surrounding quotes."""


def select_representative(posts: list[str]) -> str:
    """Pick the post whose shape is most typical of the set.

    Used for the "combined" blend. Averaging the *numbers* across posts is what
    produced bland output in the first place, so rather than synthesising a mean
    shape that no real post has, we pick the real post closest to the middle of
    the distribution — a blend in spirit, a genuine structure in practice.
    """
    if not posts:
        raise ValueError("Need at least one post to choose a representative")

    usable = [p for p in posts if p and p.strip()]
    if not usable:
        raise ValueError("Need at least one non-empty post to choose a representative")
    if len(usable) == 1:
        return usable[0]

    skeletons = [(p, extract_skeleton(p)) for p in usable]
    median_blocks = statistics.median(s.total_blocks for _, s in skeletons)
    median_words = statistics.median(s.total_words for _, s in skeletons)

    def distance(item: tuple[str, LayoutSkeleton]) -> tuple[float, float]:
        _, sk = item
        return (abs(sk.total_blocks - median_blocks), abs(sk.total_words - median_words))

    return min(skeletons, key=distance)[0]


async def _load_posts(
    db: AsyncSession,
    profile_slug: str,
    selected_posts: list[str] | None,
) -> list[str]:
    """Resolve the reference set the caller asked for."""
    if selected_posts:
        posts: list[str] = []
        for post_ref in selected_posts:
            parts = post_ref.split("/")
            if len(parts) != 2:
                continue
            p_id = (
                await db.execute(
                    select(ReferenceProfile.id).where(ReferenceProfile.slug == parts[0])
                )
            ).scalar_one_or_none()
            if not p_id:
                continue
            text = (
                await db.execute(
                    select(ReferencePost.full_text).where(
                        ReferencePost.profile_id == p_id,
                        ReferencePost.filename == parts[1],
                    )
                )
            ).scalar_one_or_none()
            if text:
                posts.append(text)
        if not posts:
            raise ValueError("No valid selected posts found in database.")
        return posts

    if profile_slug == "combined":
        result = await db.execute(select(ReferencePost.full_text))
        return list(result.scalars().all())

    profile = (
        await db.execute(
            select(ReferenceProfile).where(ReferenceProfile.slug == profile_slug)
        )
    ).scalar_one_or_none()
    if not profile:
        raise ValueError(f"Reference profile '{profile_slug}' not found.")

    result = await db.execute(
        select(ReferencePost.full_text).where(ReferencePost.profile_id == profile.id)
    )
    return list(result.scalars().all())


def build_prompt(
    topic: str,
    exemplar: str,
    user_notes: str,
    style,
    skeleton: LayoutSkeleton,
    hook_style: str | None = None,
    word_type: str | None = None,
    variation_index: int = 0,
) -> str:
    chosen_hook = hook_style if (hook_style and hook_style != "auto") else style.hook_style

    tone_hint = "Professional, engaging, and personal"
    if skeleton.total_words / max(skeleton.total_blocks, 1) < 12:
        tone_hint += " with concise, high-impact statements"

    cta_hint = (
        "ends with a question or call to action"
        if style.has_cta_pattern
        else "ends naturally, without an explicit call to action"
    )

    prompt = STYLE_PROMPT_TEMPLATE.format(
        topic=topic,
        layout_template=render_template(skeleton),
        exemplar=exemplar.strip(),
        tone_hint=tone_hint,
        hook_style=chosen_hook.replace("_", " "),
        cta_hint=cta_hint,
        emoji_frequency=style.emoji_frequency,
        avg_hashtag_count=style.avg_hashtag_count,
        common_hashtags=", ".join(style.common_hashtags) if style.common_hashtags else "none",
        user_notes=user_notes or "No additional notes — use your judgement.",
    )

    vocabulary = {
        "simple_direct": "Use simple, clear, and direct vocabulary. No jargon or corporate speak.",
        "sophisticated": "Use sophisticated, elevated vocabulary.",
        "technical": "Use precise, domain-specific vocabulary suited to practitioners.",
        "engaging": "Use warm, conversational vocabulary suited to storytelling.",
    }
    if word_type and word_type in vocabulary:
        prompt += f"\n\n{vocabulary[word_type]}"

    if variation_index == 1:
        prompt += "\n\nAngle: lead with data — stats, metrics, concrete numbers."
    elif variation_index == 2:
        prompt += "\n\nAngle: lead with narrative — a personal story or lesson learned."

    return prompt


async def generate_with_layout(
    topic: str,
    exemplar: str,
    user_notes: str = "",
    style=None,
    hook_style: str | None = None,
    word_type: str | None = None,
    variation_index: int = 0,
    ai_service: AIService | None = None,
) -> tuple[str, SimilarityReport]:
    """Generate one post cloned from `exemplar`'s structure.

    Retries while the similarity gate rejects the draft, then gives up rather
    than shipping something too close to the source. Returning the report — not
    just the text — is deliberate: the caller surfaces the score so the
    threshold gets tuned against real output instead of guessed at.
    """
    skeleton = extract_skeleton(exemplar)
    style = style or extract_style_profile([exemplar])
    ai = ai_service or AIService(provider="gemini")

    prompt = build_prompt(
        topic=topic,
        exemplar=exemplar,
        user_notes=user_notes,
        style=style,
        skeleton=skeleton,
        hook_style=hook_style,
        word_type=word_type,
        variation_index=variation_index,
    )

    last_report: SimilarityReport | None = None
    attempt_prompt = prompt

    for attempt in range(settings.similarity_max_retries + 1):
        raw = await ai.generate_with_gemini(attempt_prompt)

        # AIService falls back to a canned marketing template when the API key is
        # missing or every model errors. That text is unrelated to the exemplar,
        # so the similarity gate would wave it straight through — the user would
        # receive "Excited to share my latest insights on..." believing it was
        # generated for them. Catch it here instead.
        if is_template_fallback(raw):
            raise ValueError(
                "Text generation is unavailable — the AI service returned placeholder "
                "content. Check GEMINI_API_KEY and model availability."
            )

        shaped = enforce_layout(raw, skeleton)
        report = check_similarity(shaped, exemplar)
        last_report = report

        if report.passed:
            if attempt:
                log.info("Similarity gate passed on retry", attempt=attempt, jaccard=report.jaccard)
            return shaped, report

        log.warning(
            "Similarity gate rejected a draft",
            attempt=attempt,
            jaccard=report.jaccard,
            longest_run=report.longest_run,
        )
        attempt_prompt = (
            prompt
            + "\n\nIMPORTANT: your previous attempt reused wording from the structure "
            f"reference ({report.reason}). Keep the same shape but replace the "
            "vocabulary and imagery entirely."
        )

    # Surfaced rather than silently returned: publishing a near-copy under the
    # user's own name is a worse outcome than failing loudly.
    raise ValueError(
        "Could not generate a sufficiently original post after "
        f"{settings.similarity_max_retries + 1} attempts. "
        f"Last result: {last_report.reason if last_report else 'unknown'}"
    )


async def generate_styled_post(
    topic: str,
    user_notes: str,
    db: AsyncSession,
    profile_slug: str = "combined",
    selected_posts: list[str] | None = None,
    num_words: int | None = None,
    num_paragraphs: int | None = None,
    variation_index: int = 0,
    hook_style: str | None = None,
    line_rhythm: str | None = None,
    word_type: str | None = None,
) -> str:
    """Backwards-compatible entry point behind POST /generate/styled-post.

    num_words / num_paragraphs / line_rhythm are accepted for API compatibility
    but no longer steer the output: the exemplar's own skeleton now decides
    length and paragraphing, which is the entire point of the rewrite. Callers
    wanting a different shape should select a different exemplar.
    """
    posts = await _load_posts(db, profile_slug, selected_posts)
    if not posts:
        raise ValueError("No reference posts found.")

    exemplar = select_representative(posts)
    style = extract_style_profile(posts)

    text, _report = await generate_with_layout(
        topic=topic,
        exemplar=exemplar,
        user_notes=user_notes,
        style=style,
        hook_style=hook_style,
        word_type=word_type,
        variation_index=variation_index,
    )
    return text
