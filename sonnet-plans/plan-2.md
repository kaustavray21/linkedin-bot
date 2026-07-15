# Plan: Style-Aware Post & Image Generation for linkedin-bot

## Goal

Before you post, the bot reads from a locally-curated set of reference posts (creators/styles you admire), extracts the _pattern_ of what makes them work (hook style, structure, tone, hashtag habits), and uses that pattern to generate an original post + a matching AI image — instead of writing generic content from scratch every time.

---

## Reference storage: your existing file layout

You've already done Phase 1 by hand — copied 10 posts per profile into local `.txt` files, one folder per source, with a `linkedin_id.json` alongside them:

```
app/references/
  sub1/
    linkedin_id.json      # metadata: profile url / id for this source
    ref-1.txt ... ref-10.txt
  sub2/
    linkedin_id.json
    ref-1.txt ... ref-10.txt
```

This is the right approach — nothing here talks to LinkedIn programmatically, so there's no ToS/scraping exposure. The app should **read directly from this folder at runtime** rather than re-storing the text in a database table. That keeps raw copied text out of your DB entirely (it only ever exists as local files you control), and the app only persists _structural_ style signals, never the source sentences.

**Managing what's in `references/` stays manual** — add or remove `.txt` files yourself. No API endpoint writes or deletes here, by design.

---

## High-level flow

1. **You curate references** (done, manually) — `.txt` files per profile under `app/references/`.
2. **Reference loader** reads those files + `linkedin_id.json` metadata at request time (or on a cache refresh).
3. **Style extraction** analyzes the loaded posts and produces a `StyleProfile` (structural fingerprint) — never a copy of the text itself.
4. **You request a new post** on a topic — the bot combines the topic + the relevant `StyleProfile` and asks your existing AI service to write an **original** post inspired by that style.
5. **Image prompt builder** takes the generated post text and derives a structured image prompt (subject, action, style, background, color, framing, mood) and sends it to your existing image generation pipeline.
6. **Draft returned** to your existing posts/scheduler flow for review, editing, and publish.

---

## New components (mapped to your existing repo structure)

```
app/references/                    (existing — your curated .txt files, untouched by the app)
app/services/
  reference_loader.py              (new) reads app/references/*, no DB writes
  style_service.py                 (new) extracts StyleProfile from loaded posts
  content_generation_service.py    (new) builds prompt, calls AIService
  image_prompt_service.py          (new) builds the 7-element image prompt
app/schemas/
  reference.py                     (new) StyleProfileResponse, ReferenceProfileSummary
app/api/
  reference.py                     (new) READ-ONLY endpoints — list profiles, view style profile
  generate.py                      (extend) new POST /generate/styled-post
tests/                             (existing top-level folder — new tests live here, not under app/)
  test_reference_loader.py
  test_style_service.py
```

Reference posts and any generated `StyleProfile` never live in the same place as your ORM models — this is intentionally a read-only, file-backed feature, not a new CRUD resource.

---

## Data model

No new table for raw reference text. Optionally, cache the _computed_ profile (structural only) so you're not re-parsing files on every request:

```python
# app/database/models.py — optional cache, safe to skip in v1

class StyleProfileCache(Base):
    __tablename__ = "style_profile_cache"
    id = Column(UUID, primary_key=True, default=uuid4)
    profile_slug = Column(String)        # "sub1", "sub2", or "combined"
    tone = Column(String)
    hook_style = Column(String)
    line_rhythm = Column(String)
    avg_word_count = Column(Float)
    avg_hashtag_count = Column(Float)
    common_hashtags = Column(JSON)
    emoji_frequency = Column(String)
    has_cta_pattern = Column(Boolean)
    sample_count = Column(Integer)
    computed_at = Column(DateTime, default=utcnow)
```

If you skip this, `style_service.extract_style_profile()` just runs fresh each time you generate a post — fine at this scale (20 reference posts total).

---

## `reference_loader.py` and `style_service.py`

Implemented below as starting files — they scan `app/references/`, load each profile's metadata + post text, and turn that into a structural `StyleProfile` (word/line counts, hook pattern, hashtag habits, emoji frequency, CTA pattern). No network calls, no writes back to `references/`.

---

## `content_generation_service.py` — prompt template

```python
PROMPT_TEMPLATE = """
Write an original LinkedIn post about: {topic}

Match this style (structure and tone only — do not reuse any specific
phrases, stories, or claims from any reference post):
- Tone: {tone}
- Opening hook style: {hook_style}
- Structure: {line_rhythm}, ~{avg_word_count} words
- Typical hashtag count/style: {avg_hashtag_count} tags, e.g. {common_hashtags}
- Closing pattern: {"ends with a question/CTA to the audience" if has_cta_pattern else "no explicit CTA"}

The post must be entirely original content based on the user's own
input/angle on the topic below. Do not copy sentences from any reference
material.

User's angle/notes on this topic: {user_notes}
"""
```

Calls your existing `AIService` (Gemini) the same way `generate.py` already does — just with a style-conditioned prompt instead of a generic one.

---

## `image_prompt_service.py` — structured image prompt

Derives the 7-element prompt from the generated post text, then hands it to your existing image generation call:

```python
IMAGE_PROMPT_TEMPLATE = """
Subject: {subject}        # main focus, specific not vague
Action: {action}          # what's happening — verbs, context
Style: {style}            # e.g. photorealistic, watercolor, 3D render
Background: {background}  # setting, time of day, lighting
Color: {color}            # palette — keep consistent across a series
Framing: {framing}        # wide shot / close-up / birds-eye / centered
Mood: {mood}              # emotional register of the image
"""
```

`AIService` is prompted with the generated post text and asked to propose values for each of the seven fields, then the filled template goes to your existing Gemini/Pollinations image call.

---

## API additions (read-only for references, by design)

| Endpoint                          | Method | Purpose                                                               |
| --------------------------------- | ------ | --------------------------------------------------------------------- |
| `/reference/profiles`             | GET    | List loaded reference profiles (slug + metadata + post count)         |
| `/reference/style-profile/{slug}` | GET    | View the current extracted StyleProfile for one profile or `combined` |
| `/generate/styled-post`           | POST   | `{topic, notes, profile_slug}` → returns draft post text              |
| `/generate/styled-image`          | POST   | `{post_text}` → returns generated image                               |

No `POST`/`DELETE` on references — adding or removing a source is a filesystem action you do yourself, not an API call.

`/generate/styled-post` and `/generate/styled-image` should return drafts into your existing `PostService` create-draft flow, so scheduling/editing/publishing all reuse what you've already built.

---

## Implementation phases

1. **Reference curation** — ✅ done, via your manual local `.txt` files.
2. **Reference loader + style extraction** — `reference_loader.py` + `style_service.py` (drafted below).
3. **Styled content generation** — wire `content_generation_service.py` into `generate.py`.
4. **Structured image prompts** — `image_prompt_service.py`, wire into existing image gen.
5. _(Optional, later)_ Feedback loop — thumbs up/down on generated drafts to refine which reference profile/style gets weighted more.

---

## Open decisions for you

- Generate style per-profile (`sub1` vs `sub2` separately) or blended into one combined style? (Loader below supports both — `load_reference_profiles()` per-profile, `load_all_posts()` blended.)
- Cache computed `StyleProfile` in DB, or recompute on each request? (Fine to recompute at 20 posts total.)
- Should generated drafts require your approval before scheduling? (Recommended: yes, always.)

---

## Compliance recap

- No LinkedIn scraping or automation — reference posts are files you copied in yourself.
- Raw reference text never enters the database — it's read from disk at request time only.
- No delete/write API on references — management is a manual filesystem action.
- Generation is explicitly prompted to produce original phrasing, never to reuse source sentences.
- Tests for this feature live in the existing top-level `tests/` folder, consistent with the rest of the project.
