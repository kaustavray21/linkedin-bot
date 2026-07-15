"""
app/services/content_generation_service.py

Generates original post content that matches a structural StyleProfile.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.services.ai_service import AIService
from app.services.style_service import extract_style_profile
from app.database.models import ReferenceProfile, ReferencePost

STYLE_PROMPT_TEMPLATE = """
Write an original LinkedIn post about: {topic}

Match this style (structure and tone only — do not copy any phrases, stories, or claims from reference materials):
- Tone: {tone_hint}
- Opening hook style: {hook_style}
- Structure: {line_rhythm}, average {avg_word_count} words
- Hashtag count/style: average {avg_hashtag_count} tags, common tags to draw inspiration from: {common_hashtags}
- Closing: {cta_hint}
- Emoji usage frequency: {emoji_frequency}

The post must be entirely original content based on the user's angle/notes below.
User's angle/notes on this topic: {user_notes}

Remember: Never copy sentences from reference posts. Just capture the format/tone rhythm and hashtags pattern.
"""


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
    word_type: str | None = None
) -> str:
    """Generates a LinkedIn post for the given topic matching the style of the specified profile or individual posts from the database with optional constraints."""
    # 1. Load posts from DB
    if selected_posts:
        posts = []
        for post_ref in selected_posts:
            parts = post_ref.split("/")
            if len(parts) == 2:
                p_stmt = select(ReferenceProfile.id).where(ReferenceProfile.slug == parts[0])
                p_id = (await db.execute(p_stmt)).scalar_one_or_none()
                if p_id:
                    post_stmt = select(ReferencePost.full_text).where(
                        ReferencePost.profile_id == p_id,
                        ReferencePost.filename == parts[1]
                    )
                    text = (await db.execute(post_stmt)).scalar_one_or_none()
                    if text:
                        posts.append(text)
        if not posts:
            raise ValueError("No valid selected posts found in database.")
    elif profile_slug == "combined":
        stmt = select(ReferencePost.full_text)
        result = await db.execute(stmt)
        posts = list(result.scalars().all())
    else:
        profile_stmt = select(ReferenceProfile).where(ReferenceProfile.slug == profile_slug)
        profile = (await db.execute(profile_stmt)).scalar_one_or_none()
        if not profile:
            raise ValueError(f"Reference profile '{profile_slug}' not found.")
        
        posts_stmt = select(ReferencePost.full_text).where(ReferencePost.profile_id == profile.id)
        posts = list((await db.execute(posts_stmt)).scalars().all())

    if not posts:
        raise ValueError("No reference posts found.")

    # 2. Extract style profile
    style = extract_style_profile(posts)

    # 3. Choose styles to use, prioritizing manual overrides
    chosen_hook = hook_style if (hook_style and hook_style != "auto") else style.hook_style
    chosen_rhythm = line_rhythm if (line_rhythm and line_rhythm != "auto") else style.line_rhythm

    # Derive some hints based on style parameters
    tone_hint = "Professional, engaging, and personal"
    if chosen_rhythm == "short_punchy":
        tone_hint += " with concise, high-impact statements"

    cta_hint = "ends with a question or Call to Action (CTA) to the audience" if style.has_cta_pattern else "ends naturally without an explicit CTA question"

    # Determine word count target
    target_words = num_words if num_words is not None else style.avg_word_count

    # 4. Fill the prompt template
    prompt = STYLE_PROMPT_TEMPLATE.format(
        topic=topic,
        tone_hint=tone_hint,
        hook_style=chosen_hook.replace("_", " "),
        line_rhythm=chosen_rhythm.replace("_", " "),
        avg_word_count=target_words,
        avg_hashtag_count=style.avg_hashtag_count,
        common_hashtags=", ".join(style.common_hashtags) if style.common_hashtags else "none",
        cta_hint=cta_hint,
        emoji_frequency=style.emoji_frequency,
        user_notes=user_notes or "No additional notes.",
    )

    if num_words is not None:
        prompt += f"\nNote: The post MUST be approximately {num_words} words."
        
    if num_paragraphs is not None:
        prompt += f"\nNote: The post MUST be structured into exactly {num_paragraphs} paragraph blocks."
    else:
        prompt += "\nNote: The paragraphing structure MUST match the styling of the reference posts (e.g. if the references use single-sentence paragraphs separated by empty lines, you must format the output exactly with those same spacing and paragraphing styles)."

    # Apply word type constraints
    if word_type and word_type != "auto":
        if word_type == "simple_direct":
            prompt += "\nNote: Use simple, clear, and direct vocabulary. Avoid complex jargon, flowery words, or corporate speak."
        elif word_type == "sophisticated":
            prompt += "\nNote: Use sophisticated, intellectual, and elevated vocabulary."
        elif word_type == "technical":
            prompt += "\nNote: Use precise, technical, and domain-specific vocabulary suitable for industry experts."
        elif word_type == "engaging":
            prompt += "\nNote: Use highly conversational, warm, and engaging vocabulary suited for relatable storytelling."

    # Handle variation index instructions
    if variation_index == 1:
        prompt += "\nMake this variation more data-driven, highlighting stats, metrics, or factual statements."
    elif variation_index == 2:
        prompt += "\nMake this variation more narrative-driven, focusing on a personal story, lesson learned, or reflection."

    # 5. Call AIService to generate content using Gemini
    ai_service = AIService(provider="gemini")
    return await ai_service.generate_with_gemini(prompt)
