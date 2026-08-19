"""
app/services/post_type_service.py

Classifies a post into a type, and lets the taxonomy grow itself.

The model is given the current taxonomy and either picks a type from it or coins
a new one. A coined type registers itself — no approval step. That is the design;
what follows is not about asking permission but about keeping the result useful,
because an unconstrained taxonomy has a specific and fatal failure mode: it grows
`storytelling`, `personal_story` and `narrative` as three separate types inside a
week, and classification stops carrying information.

Six guards bound that growth:

  1. Slug normalisation      "Personal Story" and personal_story are one row
  2. Near-duplicate snap     close enough to an existing type -> use that instead
  3. Justification required  no stated reason why the existing N fail -> no type
  4. Growth brake            past ~20 active types the bar to coin one rises
  5. Usage decay             coinages unused for 90 days surface for merging
  6. Fallback detection      a canned AI response never becomes a type

Guard 6 is the non-negotiable one. AIService returns placeholder marketing copy
when Gemini is unconfigured or every model errors. With auto-registration and no
human in the loop, that text becomes a permanent post type named after "Excited
to share my latest insights" — a wrong row that then pollutes every subsequent
classification, since the taxonomy is fed back into the prompt.

## Why this splits in two

`propose_type()` touches no database and is safe to run concurrently.
`resolve_proposal()` reads and writes and must be called from one task at a time.

Discovery classifies a whole wave of posts at once, and an AsyncSession shared
across concurrent tasks raises "Session is already flushing" — the same lesson
the parallel fetcher already paid for. Propose in parallel, resolve serially.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logger import get_logger
from app.database.models import PostType
from app.services.ai_service import AIService, is_template_fallback

log = get_logger(tag="post_type")

# Short, generic words carry no signal when comparing one type to another —
# every description contains "post" and "the".
_STOPWORDS = frozenset({
    "a", "an", "and", "the", "of", "to", "in", "on", "for", "with", "that",
    "this", "it", "is", "are", "as", "by", "or", "at", "from", "post", "author",
    "reader", "linkedin", "their", "its", "who", "what", "something",
})

_WORD_RE = re.compile(r"[a-z0-9]+")

# A justification has to say something. Anything this short is the model
# satisfying the field rather than answering the question.
_MIN_JUSTIFICATION_CHARS = 20

# Prefix comparison below this length matches unrelated words — "car"/"card".
_MIN_STEM = 4

# Plurals formed with -es rather than -s.
_SIBILANTS = ("s", "x", "z", "ch", "sh")


@dataclass
class TypeProposal:
    """What the model came back with, before anything has been written."""

    existing_slug: str | None = None
    slug: str | None = None
    label: str | None = None
    description: str | None = None
    why_new: str | None = None
    refused: str | None = None

    @property
    def is_new(self) -> bool:
        return self.refused is None and self.existing_slug is None and bool(self.slug)


@dataclass
class Resolution:
    """What actually happened once the proposal met the database."""

    slug: str | None = None
    created: bool = False
    snapped_to: str | None = None
    refused: str | None = None
    notes: list[str] = field(default_factory=list)


# ------------------------------------------------------------- normalisation --

def normalise_slug(raw: str) -> str:
    """Guard 1. Fold surface variants of the same name onto one key.

    Lowercased, non-alphanumerics collapsed to underscores, and a trailing
    plural stripped from the final token so `listicles` and `listicle` cannot
    both exist.
    """
    slug = _WORD_RE.findall((raw or "").lower())
    if not slug:
        return ""
    last = slug[-1]
    if len(last) > 3 and last.endswith("ies"):
        slug[-1] = last[:-3] + "y"                    # stories -> story
    elif len(last) > 3 and last.endswith("es") and last[:-2].endswith(_SIBILANTS):
        # Only after a sibilant, where the e is inserted to make the plural
        # sayable: boxes -> box. Applying it everywhere turns listicles into
        # listicl, a slug that then matches nothing ever again.
        slug[-1] = last[:-2]
    elif len(last) > 3 and last.endswith("s") and not last.endswith("ss"):
        slug[-1] = last[:-1]                          # listicles -> listicle
    return "_".join(slug)[:50]


def _tokens(text: str) -> set[str]:
    return {w for w in _WORD_RE.findall((text or "").lower()) if w not in _STOPWORDS}


def _stems_match(a: str, b: str) -> bool:
    if a == b:
        return True
    shorter, longer = (a, b) if len(a) <= len(b) else (b, a)
    return len(shorter) >= _MIN_STEM and longer.startswith(shorter)


def _token_containment(a: set[str], b: set[str]) -> float:
    """How much of the smaller token set is present in the larger.

    Containment rather than Jaccard on purpose: `personal_story` against `story`
    scores 0.5 by Jaccard and 1.0 by containment, and the second is the useful
    answer — one is a narrower name for the other.
    """
    if not a or not b:
        return 0.0
    smaller, larger = (a, b) if len(a) <= len(b) else (b, a)
    hits = sum(1 for token in smaller if any(_stems_match(token, o) for o in larger))
    return hits / len(smaller)


def proposal_similarity(
    slug: str, label: str, description: str,
    other_slug: str, other_label: str, other_description: str,
) -> float:
    """Guard 2's score: how close a proposed type is to an existing one.

    Two views, and the stronger wins. Slug tokens catch a narrower name for the
    same idea (`personal_story` / `story`); label-and-description tokens catch
    the same idea described in different words.

    This finds paraphrase and narrowing. It does not find synonymy — nothing
    here relates `narrative` to `story`, because doing that properly needs a
    stemmer or embeddings. The growth brake and the merge pass are the backstop
    for what gets through, which is why they exist rather than being optional.
    """
    by_slug = _token_containment(_tokens(slug.replace("_", " ")),
                                 _tokens(other_slug.replace("_", " ")))
    by_text = _token_containment(_tokens(f"{label} {description}"),
                                 _tokens(f"{other_label} {other_description}"))
    return max(by_slug, by_text)


# -------------------------------------------------------------------- prompt --

def build_classification_prompt(text: str, taxonomy: list[dict]) -> str:
    listing = "\n".join(
        f"- {t['slug']}: {t['label']} — {t.get('description') or 'no description'}"
        for t in taxonomy
    ) or "- (none yet)"

    return f"""Classify this LinkedIn post into one post type.

Existing types:
{listing}

Post:
\"\"\"
{text.strip()[:4000]}
\"\"\"

Reply with JSON only, no prose and no code fence.

If one of the existing types fits, even loosely:
{{"existing_slug": "<slug from the list>"}}

Only if none of them fit, propose one:
{{"slug": "<lower_snake_case>", "label": "<2-4 words>",
  "description": "<one sentence describing the type, not this post>",
  "why_new": "<why each existing type above fails — be specific>"}}

Prefer an existing type. A new type is justified only when the post's
*structure and intent* differ from every type listed, not when its topic is
merely unusual."""


# ------------------------------------------------------------------ proposal --

def _parse_reply(raw: str) -> TypeProposal:
    cleaned = raw.strip()
    # Models fence JSON despite being asked not to.
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[a-z]*\s*|\s*```$", "", cleaned, flags=re.IGNORECASE)

    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start == -1 or end <= start:
        return TypeProposal(refused="the classifier reply was not JSON")

    try:
        data = json.loads(cleaned[start:end + 1])
    except json.JSONDecodeError:
        return TypeProposal(refused="the classifier reply was not valid JSON")
    if not isinstance(data, dict):
        return TypeProposal(refused="the classifier reply was not an object")

    existing = data.get("existing_slug")
    if isinstance(existing, str) and existing.strip():
        return TypeProposal(existing_slug=normalise_slug(existing))

    slug = data.get("slug")
    if not isinstance(slug, str) or not slug.strip():
        return TypeProposal(refused="the classifier named neither an existing nor a new type")

    return TypeProposal(
        slug=normalise_slug(slug),
        label=(data.get("label") or slug).strip()[:100],
        description=(data.get("description") or "").strip() or None,
        why_new=(data.get("why_new") or "").strip() or None,
    )


async def propose_type(
    text: str,
    taxonomy: list[dict],
    ai_service: AIService | None = None,
) -> TypeProposal:
    """Ask the model to classify. Touches no database — safe to run concurrently.

    Refusal is a normal outcome, not an error. An unclassified post is a post
    with no type; a wrongly classified one corrupts the taxonomy that every
    later classification is prompted with.
    """
    if not text or not text.strip():
        return TypeProposal(refused="the post has no readable text")

    ai = ai_service or AIService(provider="gemini")

    try:
        raw = await ai.generate_with_gemini(build_classification_prompt(text, taxonomy))
    except Exception as exc:
        log.warning("Classifier call failed", error=str(exc))
        return TypeProposal(refused=f"the classifier was unavailable: {exc}")

    # Guard 6, and the reason this check cannot be skipped. AIService degrades to
    # canned marketing copy rather than raising, so without this the taxonomy
    # gains a type named after a template — permanently, and in every subsequent
    # prompt.
    if is_template_fallback(raw):
        log.warning("Classifier returned placeholder copy; refusing to classify")
        return TypeProposal(refused="the classifier was unavailable (placeholder response)")

    proposal = _parse_reply(raw)

    # Guard 3. A new type has to be argued for. Coining one "because it felt
    # different" is how near-synonyms get in.
    if proposal.is_new:
        if not proposal.why_new or len(proposal.why_new) < _MIN_JUSTIFICATION_CHARS:
            return TypeProposal(
                refused="a new type was proposed without saying why the existing ones fail"
            )

    return proposal


# ----------------------------------------------------------------- resolution --

async def load_taxonomy(db: AsyncSession) -> list[dict]:
    """The active types, as plain dicts.

    Plain dicts rather than ORM objects because this crosses into concurrent
    tasks, where a detached instance would lazy-load against a session another
    coroutine is using.
    """
    rows = (
        await db.execute(
            select(PostType).where(PostType.active.is_(True)).order_by(PostType.slug)
        )
    ).scalars().all()
    return [
        {"slug": r.slug, "label": r.label, "description": r.description}
        for r in rows
    ]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


async def _get_by_slug(db: AsyncSession, slug: str) -> PostType | None:
    return (
        await db.execute(select(PostType).where(PostType.slug == slug))
    ).scalar_one_or_none()


async def _record_use(db: AsyncSession, post_type: PostType) -> None:
    post_type.usage_count = (post_type.usage_count or 0) + 1
    post_type.last_used_at = _utcnow()
    db.add(post_type)


async def resolve_proposal(db: AsyncSession, proposal: TypeProposal) -> Resolution:
    """Turn a proposal into a stored type. Writes — call this serially.

    Nothing is flushed for a refusal, so a failed classification leaves the
    taxonomy exactly as it was.
    """
    if proposal.refused:
        return Resolution(refused=proposal.refused)

    if proposal.existing_slug:
        existing = await _get_by_slug(db, proposal.existing_slug)
        if existing is None:
            # The model invented a slug and presented it as one of ours. Treated
            # as a miss rather than silently created: it never argued for a new
            # type, so guard 3 was never applied to it.
            return Resolution(
                refused=f"the classifier chose a type that does not exist: {proposal.existing_slug}"
            )
        if not existing.active and existing.merged_into_id:
            merged = await db.get(PostType, existing.merged_into_id)
            if merged is not None:
                await _record_use(db, merged)
                return Resolution(slug=merged.slug, snapped_to=merged.slug,
                                  notes=[f"{existing.slug} was merged into {merged.slug}"])
        await _record_use(db, existing)
        return Resolution(slug=existing.slug)

    if not proposal.slug:
        return Resolution(refused="the classifier named no type")

    # Guard 1 already normalised the slug; an exact hit is the same type.
    exact = await _get_by_slug(db, proposal.slug)
    if exact is not None:
        await _record_use(db, exact)
        return Resolution(slug=exact.slug, snapped_to=exact.slug)

    taxonomy = await load_taxonomy(db)

    # Guard 4. The bar to coin a type rises with the size of the taxonomy: past
    # the brake, the snap threshold drops so more proposals fold into what is
    # already there.
    threshold = settings.post_type_snap_threshold
    notes: list[str] = []
    if len(taxonomy) >= settings.post_type_growth_brake:
        threshold = settings.post_type_brake_snap_threshold
        notes.append(
            f"{len(taxonomy)} active types — novelty bar raised; a merge pass is due"
        )

    # Guard 2.
    best_slug, best_score = None, 0.0
    for candidate in taxonomy:
        score = proposal_similarity(
            proposal.slug, proposal.label or "", proposal.description or "",
            candidate["slug"], candidate["label"], candidate.get("description") or "",
        )
        if score > best_score:
            best_slug, best_score = candidate["slug"], score

    if best_slug and best_score >= threshold:
        snapped = await _get_by_slug(db, best_slug)
        if snapped is not None:
            await _record_use(db, snapped)
            return Resolution(
                slug=snapped.slug, snapped_to=snapped.slug,
                notes=notes + [
                    f"proposed '{proposal.slug}' folded into '{snapped.slug}' "
                    f"(similarity {best_score:.2f})"
                ],
            )

    created = PostType(
        slug=proposal.slug,
        label=proposal.label or proposal.slug.replace("_", " ").title(),
        description=proposal.description,
        origin="ai",
        why_new=proposal.why_new,
        usage_count=1,
        last_used_at=_utcnow(),
        active=True,
    )
    db.add(created)
    log.info("New post type registered", slug=created.slug, why=created.why_new)
    return Resolution(slug=created.slug, created=True, notes=notes)


# ---------------------------------------------------------------------- decay --

async def stale_types(db: AsyncSession, days: int | None = None) -> list[PostType]:
    """Guard 5. Model-coined types nobody has used lately.

    Seeded types are never stale — they are the vocabulary the taxonomy is meant
    to have, whether or not this month's posts happen to use them. A type never
    used at all counts from when it was first seen.
    """
    window = days if days is not None else settings.post_type_decay_days
    cutoff = _utcnow() - timedelta(days=window)

    rows = (
        await db.execute(
            select(PostType).where(
                PostType.active.is_(True),
                PostType.origin == "ai",
            )
        )
    ).scalars().all()

    return [
        t for t in rows
        if (t.last_used_at or t.first_seen_at) is not None
        and (t.last_used_at or t.first_seen_at) < cutoff
    ]


# ---------------------------------------------------------------- merge pass --

@dataclass
class MergeProposal:
    """A suggestion, never an action. Merging is destructive enough to ask."""

    loser_slug: str
    winner_slug: str | None          # None means retire rather than merge
    reason: str
    similarity: float | None = None
    loser_usage: int = 0
    winner_usage: int = 0
    loser_origin: str = "ai"


def _pick_loser(a: PostType, b: PostType) -> tuple[PostType, PostType]:
    """Which of two near-identical types survives.

    A seeded type always outlives a coined one — the seeds are the vocabulary
    the taxonomy is meant to have. Otherwise the better-used name wins, and an
    exact tie goes to whichever existed first.
    """
    if a.origin != b.origin:
        return (b, a) if a.origin == "seed" else (a, b)
    if (a.usage_count or 0) != (b.usage_count or 0):
        return (a, b) if (a.usage_count or 0) < (b.usage_count or 0) else (b, a)
    return (b, a) if a.id < b.id else (a, b)


async def merge_proposals(db: AsyncSession) -> list[MergeProposal]:
    """Guards 4 and 5, surfaced. Nothing here writes.

    Two sources: types close enough that keeping both makes the classification
    ambiguous, and coined types nobody has used inside the decay window. The
    second is why usage is tracked at all — a model that coins a type for one
    post and never reaches for it again has added noise, not vocabulary.
    """
    rows = (
        await db.execute(
            select(PostType).where(PostType.active.is_(True)).order_by(PostType.id)
        )
    ).scalars().all()

    proposals: list[MergeProposal] = []
    paired: set[tuple[str, str]] = set()

    for i, a in enumerate(rows):
        for b in rows[i + 1:]:
            score = proposal_similarity(
                a.slug, a.label, a.description or "",
                b.slug, b.label, b.description or "",
            )
            if score < settings.post_type_snap_threshold:
                continue
            loser, winner = _pick_loser(a, b)
            key = (loser.slug, winner.slug)
            if key in paired:
                continue
            paired.add(key)
            proposals.append(MergeProposal(
                loser_slug=loser.slug,
                winner_slug=winner.slug,
                reason=f"'{loser.slug}' and '{winner.slug}' describe the same kind of post",
                similarity=round(score, 2),
                loser_usage=loser.usage_count or 0,
                winner_usage=winner.usage_count or 0,
                loser_origin=loser.origin,
            ))

    already = {p.loser_slug for p in proposals}
    for stale in await stale_types(db):
        if stale.slug in already:
            continue
        # A stale type with no close neighbour is retired rather than merged:
        # folding it into something unrelated would be worse than losing it.
        best_slug, best_score = None, 0.0
        for other in rows:
            if other.slug == stale.slug:
                continue
            score = proposal_similarity(
                stale.slug, stale.label, stale.description or "",
                other.slug, other.label, other.description or "",
            )
            if score > best_score:
                best_slug, best_score = other.slug, score

        days = settings.post_type_decay_days
        if best_slug and best_score >= settings.post_type_brake_snap_threshold:
            proposals.append(MergeProposal(
                loser_slug=stale.slug, winner_slug=best_slug,
                reason=f"unused for over {days} days; closest existing type is '{best_slug}'",
                similarity=round(best_score, 2),
                loser_usage=stale.usage_count or 0,
                loser_origin=stale.origin,
            ))
        else:
            proposals.append(MergeProposal(
                loser_slug=stale.slug, winner_slug=None,
                reason=f"unused for over {days} days and close to nothing else",
                loser_usage=stale.usage_count or 0,
                loser_origin=stale.origin,
            ))

    return proposals


async def merge_types(db: AsyncSession, loser_slug: str, winner_slug: str | None) -> str:
    """Fold one type into another, or retire it. Writes — call serially.

    The loser is deactivated rather than deleted. Posts already classified into
    it are repointed here, but `merged_into_id` still has to be set: a
    classification that names the old slug afterwards must resolve to the
    survivor rather than being refused.
    """
    loser = await _get_by_slug(db, loser_slug)
    if loser is None:
        raise ValueError(f"No such post type: {loser_slug}")

    winner = None
    if winner_slug:
        winner = await _get_by_slug(db, winner_slug)
        if winner is None:
            raise ValueError(f"No such post type: {winner_slug}")
        if winner.id == loser.id:
            raise ValueError("A type cannot be merged into itself")
        if not winner.active:
            raise ValueError(f"'{winner_slug}' is not an active type")

    from app.database.models import DiscoveredPost

    posts = (
        await db.execute(
            select(DiscoveredPost).where(DiscoveredPost.post_type_slug == loser.slug)
        )
    ).scalars().all()
    for post in posts:
        post.post_type_slug = winner.slug if winner else None
        db.add(post)

    loser.active = False
    if winner is not None:
        loser.merged_into_id = winner.id
        # The survivor inherits the history, so the decay window reflects how
        # often the *idea* was used rather than which name happened to carry it.
        winner.usage_count = (winner.usage_count or 0) + (loser.usage_count or 0)
        if loser.last_used_at and (
            winner.last_used_at is None or loser.last_used_at > winner.last_used_at
        ):
            winner.last_used_at = loser.last_used_at
        db.add(winner)
    db.add(loser)

    log.info(
        "Post type merged",
        loser=loser.slug,
        winner=winner.slug if winner else None,
        posts_repointed=len(posts),
    )
    return winner.slug if winner else ""
