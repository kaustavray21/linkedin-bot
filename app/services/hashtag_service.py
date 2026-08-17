"""
app/services/hashtag_service.py

Produces hashtags in the spirit of an exemplar's, without reproducing its set.

Copying a creator's tags verbatim is the most visible form of the copying this
project is trying to avoid — it is immediately recognisable to anyone who
follows both accounts. But some tags cannot be avoided: #AI is the name of the
subject, not a coined phrase, and dropping it costs reach while gaining no
originality. So generics pass through and distinctive coinages get reworked.

Count and placement come from the exemplar; the words do not.
"""

from __future__ import annotations

import random
import re

from app.core.logger import get_logger
from app.services.ai_service import AIService, is_template_fallback

log = get_logger()

TAG_RE = re.compile(r"#\w+")

# Tags that name a field rather than express a voice. Avoiding these would make
# posts less discoverable while making them no more original.
GENERIC_TAGS = {
    "#ai", "#python", "#tech", "#technology", "#startup", "#startups",
    "#software", "#engineering", "#data", "#cloud", "#devops", "#security",
    "#leadership", "#career", "#hiring", "#linkedin", "#marketing", "#saas",
    "#productivity", "#innovation", "#business", "#design", "#ux",
}

REMIX_PROMPT = """You are helping write hashtags for a LinkedIn post.

Topic: {topic}

Here are hashtags used by a different creator on a similar topic:
{source_tags}

Produce exactly {count} hashtags for our post. Rules:
- Do NOT reuse any distinctive tag from the list above. Common industry tags
  naming the field itself (like #AI or #Python) are fine to keep.
- Match the register and length of the originals — if theirs are short and
  punchy, ours should be too.
- Every tag must be relevant to the topic.
- Return only the hashtags, space separated, on one line. Nothing else."""


def extract_tags(text: str) -> list[str]:
    return TAG_RE.findall(text or "")


def strip_trailing_hashtag_block(text: str) -> str:
    """Remove a trailing hashtags-only block from generated text.

    When the exemplar ends in a hashtag block, the layout template instructs the
    model to produce one too — so it writes its own tags. Those tags never went
    through remix_hashtags(), which is the only thing enforcing the
    no-copying rule. Observed live: the model emitted "#buildinpublic" against a
    source using "#buildinginpublic", and it shipped alongside a second,
    properly remixed block.

    Stripping the model's block and appending the policed one fixes the
    duplication and closes that bypass at the same time.
    """
    if not text:
        return text

    blocks = text.rstrip().split("\n\n")
    while blocks:
        words = blocks[-1].split()
        # Only a block that is *entirely* tags. A line ending in one trailing
        # tag is still prose and must be left alone.
        if words and all(w.startswith("#") for w in words):
            blocks.pop()
        else:
            break

    return "\n\n".join(blocks).rstrip()


def is_generic(tag: str) -> bool:
    return tag.lower() in GENERIC_TAGS


def _fallback_remix(source_tags: list[str], topic: str, count: int) -> list[str]:
    """Deterministic remix for when the model is unavailable.

    Keeps the generics, then pads from the topic's own words. Not clever, but it
    never returns the source's distinctive tags, which is the property that
    matters.
    """
    kept = [t for t in source_tags if is_generic(t)]
    words = [w for w in re.findall(r"[A-Za-z]{4,}", topic)][:count]
    derived = [f"#{w.capitalize()}" for w in words]

    out: list[str] = []
    for tag in kept + derived:
        if tag.lower() not in {o.lower() for o in out}:
            out.append(tag)
    return out[:count]


async def remix_hashtags(
    source_tags: list[str],
    topic: str,
    count: int | None = None,
    ai_service: AIService | None = None,
) -> list[str]:
    """Generate our own tags, informed by the exemplar's but not copied from it."""
    source_tags = [t for t in source_tags if t]
    target = count if count is not None else len(source_tags)
    if target <= 0:
        return []

    if not source_tags:
        return _fallback_remix([], topic, target)

    ai = ai_service or AIService(provider="gemini")
    prompt = REMIX_PROMPT.format(
        topic=topic,
        source_tags=" ".join(source_tags),
        count=target,
    )

    try:
        raw = await ai.generate_with_gemini(prompt)
    except Exception:
        log.exception("Hashtag remix failed; using deterministic fallback")
        return _fallback_remix(source_tags, topic, target)

    if is_template_fallback(raw):
        return _fallback_remix(source_tags, topic, target)

    candidates = extract_tags(raw)

    # Enforce the no-copying rule in code. The model is asked to follow it, but
    # asking is not the same as guaranteeing, and this is the whole point of the
    # feature.
    distinctive_sources = {t.lower() for t in source_tags if not is_generic(t)}
    cleaned: list[str] = []
    for tag in candidates:
        if tag.lower() in distinctive_sources:
            continue
        if tag.lower() in {c.lower() for c in cleaned}:
            continue
        cleaned.append(tag)

    if len(cleaned) < target:
        for extra in _fallback_remix(source_tags, topic, target):
            if len(cleaned) >= target:
                break
            if extra.lower() not in {c.lower() for c in cleaned}:
                cleaned.append(extra)

    cleaned = cleaned[:target]

    # Order carries a signature of its own; shuffle so ours does not mirror theirs.
    random.shuffle(cleaned)
    return cleaned


DERIVE_PROMPT = """Read this LinkedIn post and produce {count} hashtags for it.

Post:
\"\"\"{text}\"\"\"

Rules:
- Tags must describe what this post is actually about — not generic filler.
- Match the register a thoughtful practitioner would use, not a marketer.
- Prefer specific over broad where the post supports it.
- Return only the hashtags, space separated, on one line. Nothing else."""


async def derive_hashtags(
    text: str,
    count: int = 5,
    ai_service: AIService | None = None,
) -> list[str]:
    """Tags derived from your own finished post, with no exemplar involved.

    Distinct from remix_hashtags(): that one exists to avoid copying a source's
    distinctive tags, a rule that only means something when there IS a source.
    Here there is nothing to avoid — the job is reading the post and naming what
    it is about. _fallback_remix() is not a substitute either; it only
    capitalises words from a topic string.
    """
    text = (text or "").strip()
    if not text or count <= 0:
        return []

    ai = ai_service or AIService(provider="gemini")
    prompt = DERIVE_PROMPT.format(text=text[:4000], count=count)

    try:
        raw = await ai.generate_with_gemini(prompt)
    except Exception:
        log.exception("Hashtag derivation failed")
        return _fallback_remix([], text, count)

    # Without this, an unreachable Gemini turns its canned marketing copy into
    # hashtags — #Excited, #Insights — and they look deliberate.
    if is_template_fallback(raw):
        log.warning("Hashtag derivation got the AI fallback template; using words from the post")
        return _fallback_remix([], text, count)

    seen: list[str] = []
    for tag in extract_tags(raw):
        if tag.lower() not in {t.lower() for t in seen}:
            seen.append(tag)
    return seen[:count] or _fallback_remix([], text, count)
