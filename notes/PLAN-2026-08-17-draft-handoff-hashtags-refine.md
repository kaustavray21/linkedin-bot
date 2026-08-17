# Plan — Draft Handoff, a Hashtag Section, and Prompt-Driven Refinement

Date: 2026-08-17 (rev 2 — bug register consolidated; B0 executed)
Branch: `jul-9-contentGeneration-fix-branch`
Status: **B0 ✅ · R1–R5 outstanding**

## EXECUTION LOG — B0 (2026-08-17)

**141 tests pass** (130 before, +11). Backend only; no UI touched, nothing committed.

| Bug | Fix |
|---|---|
| ① published posts were editable | `ConflictException` (409) + a status guard in `update_draft`. Verified for both `published` and `publishing`, and verified *not* to lock `draft` or `scheduled` |
| ② image / schedule could not be cleared | `UNSET` sentinel. Omitted → untouched; explicit `null` → cleared. The old `scheduled_time` branch was unreachable (`if x is not None` then `... if x else ...`), so un-scheduling was impossible by construction |
| ⑤ `image_source` never written | Carried through `PostCreate` / `PostUpdate` / `PostResponse`, `create_draft`, `update_draft` and `PostRepository.create`. Route uses `model_dump(exclude_unset=True)`, which is what preserves the omitted/null distinction across the wire |

`tests/test_draft_mutations.py` — 11 cases including all three image sources, an HTTP round-trip proving `exclude_unset` survives the boundary, and a guard that drafts and scheduled posts stay editable.

**Backward compatibility:** both `update_draft` callers are in `posts.py`. Callers that omit a field behave exactly as before — the only change is that passing `null` now means something. Nothing else in the app calls `PUT /posts/{id}` yet, so the 409 cannot affect current behaviour.

**Not done here, by design:** ③ and ④ are hazards in code that does not exist yet (autosave, sidebar toggle) — they are constraints on R4, recorded in §3.6. The frontend still discards `image_source` from the media response; wiring `applyImage()` belongs with the drafts work.

Relates to: `PLAN-2026-08-17-discovery-driven-generation.md` (see §7 — both touch the Create Post view)

---

## 1. What's being asked

| | Ask |
|---|---|
| **A** | "Draft one like this" should move you to Create Post immediately, show a drafting animation, then let you edit the result section by section |
| **B** | A hashtag section in Create Post — add your own, and autogenerate from your content *or* the referenced post, sitting alongside the image section |
| **C** | In Post Body, a prompt box to change aspects of the written content, using the current style and the ideas already there |
| **D** | A drafts panel on the left of Create Post — save drafts there, and open them back up. **History holds no drafts**; the two are separate |
| **E** | Multiple drafts open at once, as tabs in the Create Post section |

---

## 2. What already exists (more than expected)

Worth reading before costing this — three of the pieces are already built and simply not surfaced.

**The endpoint already returns hashtags separately.** `RemixResponse` carries `hashtags: list[str]` as its own field (`generate.py`), but the frontend uses `result.full_text` — body *with tags already appended* — and throws the array away (`app.js`, `applyRemixResult`). The data for the hashtag section is already on the wire.

**The hashtag engine is already good.** `remix_hashtags()` (`hashtag_service.py:107`) takes source tags, a topic and a count, and enforces the no-copying rule **in code** rather than trusting the prompt (`:140-150`), with `is_template_fallback` detection and a deterministic fallback. It needs a UI, not a rewrite.

**Staged progress needs no backend work.** `RemixRequest.with_image` already exists (`generate.py`), as does `POST /generate/styled-image` taking `post_text`. So the frontend can make two fast calls instead of one slow one — text first, image second — and report each honestly.

**Draft storage is already built, and already in use.** `PostRepository.create()` defaults to `status="draft"` (`post_repository.py:17-21`), `POST /posts/` calls `create_draft`, `PUT /posts/{id}` calls `update_draft`, `GET /posts/` lists and `DELETE /posts/{id}` removes. The publish flow already goes *through* a draft: `handlePostSubmit` calls `createPost` and only then `publishPost`. Every post you have ever published was a draft row first.

So D is almost entirely a UI over CRUD that exists — plus one bug it would otherwise introduce (§3.4).

**What genuinely does not exist:** deriving hashtags from *your own* text with no exemplar, refining an existing draft, and any link from a saved draft back to the post it was cloned from.

---

## 3. Design

### 3.1 The handoff (A) — switch first, then wait

The problem is not that `applyRemixResult` fails to navigate; it does (`switchTab('create')` then `showSection('body')`). The problem is **when**: the `await API.remixPost(...)` happens while you are still looking at Discover, so you watch a disabled button through text generation, up to three similarity retries, and image generation. Then everything appears at once.

```
NOW    click ──► [ disabled button on Discover ......... 20-60s ......... ] ──► everything appears
```
```
PLAN   click ──► Create Post opens, drafting state visible
                   ├─ stage 1  writing the draft…        (~5s)  ──► body appears, editable
                   └─ stage 2  generating an image…      (~15s) ──► image appears
```

Two real calls, so the two stages are real rather than a decorative animation:

1. `remixPost(topic, exemplarId, notes, withImage=false)` → text, hashtags, exemplar metadata
2. `generateStyledImage(text)` → image

That also matches what the service already believes: an imageless draft is still useful, and image failure is a downgrade rather than an error (`remix_service.py:166-168`). Splitting the calls turns that from a note in a toast into something you can see — the draft is editable while the image is still being made.

The drafting state is a skeleton placeholder over the Post Body section, not a spinner over the whole view: you should be able to see the rail, the sections and the exemplar attribution while stage 2 runs.

**Same treatment for `Find & draft automatically`** (`generateFromTopic`), which is slower still — it runs discovery first.

### 3.2 The hashtag section (B) — and the one decision that matters

New section between Image and Publish, styled like the image section (`③ Image` → `④ Hashtags` → `⑤ Publish`):

```
④ Hashtags                                        7 tags · 62 chars
┌────────────────────────────────────────────────────────────┐
│  #BuildInPublic ×   #ShippingFast ×   #FounderLessons ×     │
│  [ + add a tag                                    ]         │
├────────────────────────────────────────────────────────────┤
│  [ ✨ From my post ]   [ ✨ From the reference ]   [ Clear ] │
└────────────────────────────────────────────────────────────┘
```

**The load-bearing decision: the textarea holds the body only; tags live in the chip list.**

Today the tags are baked into the text (`full_text` = body + tags). If they stay baked in, "edit a hashtag" means text surgery, and `strip_trailing_hashtag_block()` — which exists to remove the model's *unpoliced* tag block — starts fighting the user's own edits. Splitting them makes both sides simple, and composition mirrors the server rule already in `RemixResult.full_text`:

```js
composeFullText() = body.trimEnd() + (tags.length ? "\n\n" + tags.join(" ") : "")
```

Consequences to handle deliberately, none of them hard:

| | |
|---|---|
| `getPostState().content` | returns the **composed** text — publish, preview and lineage all see what LinkedIn will |
| Char counter | counts composed length. LinkedIn counts hashtags, so counting only the body would understate it right up to the 3000 limit |
| Preview modal | shows composed text, tags included, `pre-wrap` as now |
| Incoming remix | body ← `result.text`, chips ← `result.hashtags`. Stop using `full_text` on the client |

**Two generate buttons, because they are two different functions:**

- **From the reference** → `remix_hashtags(exemplar.hashtags, topic, count)`. Exists. Carries the no-copy rule, which only makes sense when there *is* a source to avoid copying.
- **From my post** → `derive_hashtags(body, count)`. **New.** `_fallback_remix()` is not this — it just capitalises words from the topic. Deriving tags from finished prose is a different prompt and deserves its own function, with the same `is_template_fallback` guard.

"From the reference" is disabled with a reason when the draft has no exemplar, rather than silently producing topic-word tags.

### 3.3 The refine prompt (C) — and the trap in it

A prompt box under the Post Body textarea:

```
┌──────────────────────────────────────────────────────────┐
│ Make the opening punchier and cut the third paragraph     │
│                                        [ ✨ Rewrite ]     │
└──────────────────────────────────────────────────────────┘
   Recent: "punchier hook" · "less formal" · "add a stat"
```

New service function, composed from parts that already exist:

```python
async def refine_post(
    current_text: str,
    instruction: str,
    exemplar: str | None,        # for the similarity gate
    style: StyleProfile,
    keep_shape: bool = True,
) -> tuple[str, SimilarityReport | None]
```

- Skeleton comes from **the current text**, not the exemplar — so "make it punchier" changes wording and keeps your shape. Shape changes come from the paragraph-count control, not from prose instructions.
- `enforce_layout()` applies it, exactly as generation does.
- Output goes through `strip_trailing_hashtag_block()`. With a separate tag section, a model that writes its own tags into the body would double them up.

**The trap: the similarity gate must run on every refine, against the original exemplar.**

`check_similarity` compares a draft to the exemplar it was cloned from. A refine that skips it is a slow leak — each rewrite nudges wording, and after three or four you can drift back toward the source while still carrying a "green" badge earned by the *first* draft. Publishing a near-copy under your own name is the one outcome this codebase already refuses to allow (`content_generation_service.py:279-285`).

So: the Create Post view must retain `exemplar_id` and the exemplar text for the session. `RemixResponse` already returns `exemplar_id`, `exemplar_url` and `exemplar_author` — the frontend currently discards all three.

And when there is **no** exemplar (a plain `/generate/text` draft), the gate has nothing to compare against. Say so — "originality not checked, this draft has no source post" — rather than showing a green badge that means nothing.

### 3.4 The drafts panel (D) — and the bug it would otherwise introduce

Create Post becomes four columns:

```
┌─────────┬────────────┬──────────────────────────────┬───────────┐
│ sidebar │  LIBRARY   │  ┌────────┬────────┬───┐      │   RAIL    │
│  280px  │  240px     │  │Shipping│DM open•│ + │      │   300px   │
│         │            │  └────────┴────────┴───┘      │           │
│Dashboard│ + New draft│                               │ ① AI      │
│Discover │ ─────────  │   2. Review & Edit Body       │ ▸② Body   │
│▸Create  │ ▸Shipping… │  ┌─────────────────────────┐  │ ③ Image   │
│History  │  2h ago    │  │ textarea                │  │ ④ Hashtags│
│         │ ─────────  │  └─────────────────────────┘  │ ⑤ Publish │
│         │  DM openers│                               │           │
│         │  yesterday │       [← Back]  [Next →]      │ [Review]  │
└─────────┴────────────┴──────────────────────────────┴───────────┘
              ↑                    ↑
     the library: every       the tab bar: what is
     saved draft              open right now  (• = unsaved)
```

**Library and tabs are different things** and the distinction is worth keeping sharp: the left panel lists *every* saved draft, the tab bar shows *what is currently open*. Clicking a library entry opens it as a tab; closing a tab does not delete the draft.

820px of fixed columns plus gaps, so the editor gets ~560px at 1440 — workable, tight. Below **1280px the drafts panel collapses to a toggle** rather than squeezing the editor; it is the least-used column of the four.

Each entry shows the first line as a title (posts have no title field), relative time, and badges for image / scheduled.

**The bug this would introduce if built naively.**

`handlePostSubmit` calls `API.createPost(...)` unconditionally. Open draft #7, edit it, publish — and you get a *new* row #12 published, with **#7 left behind as an orphan draft** that looks unpublished forever. Today that is invisible because nothing ever reopens a draft. The moment the panel exists, it is a data bug on every publish.

Fix: the Create view holds `currentDraftId`, and the publish path becomes

```
currentDraftId ?  PUT /posts/{id}  →  publish that id
               :  POST /posts/     →  publish the new id
```

The same discipline makes save-again update rather than duplicate — without it, every save is a new row and the panel fills with copies of one post.

**Round-trip fidelity — what survives a save/open cycle.**

| State | Survives? | How |
|---|---|---|
| Body text | ✅ | `content` |
| Hashtags | ✅ | Composed into `content` on save; **decomposed on open** with `strip_trailing_hashtag_block()` + `extract_tags()` — both already exist (`hashtag_service.py:52,56`) |
| **Image** | ✅ | `image_url` — see below |
| Scheduled time | ✅ | `scheduled_time` |
| **Image provenance** | ❌ | `posts.image_source` exists but **is never written** — bug ⑤ |
| **Exemplar** | ❌ | **Not stored anywhere.** Reopen a draft and refine has nothing to check similarity against, and "From the reference" hashtags go dead |

**On the image specifically.** `image_url` holds a path (`/static/uploads/<uuid>.png`), not the bytes — but every source resolves to a local file before it is ever referenced. AI generation writes there, uploads are re-encoded into it (`media_service.normalise_and_store`), and a remote URL is downloaded and stored locally before publishing. So a draft's image is genuinely durable: **nothing in the codebase ever deletes from `uploads/`** (verified — no `unlink`, no TTL, no cleanup job), so a reference saved today still resolves next month.

The flip side is that `uploads/` grows without bound and orphans accumulate whenever a draft holding the only reference is deleted. That is a real trade, not a bug, and I am not proposing a reaper here: a cleanup job that scans references is exactly the kind of thing that deletes a live image when it gets a query subtly wrong. Worth revisiting when the directory is large enough to notice.

The exemplar gap is the one real hole, and the discovery plan already fixes it — `draft_lineage` links `post_id` to `discovered_post_id` with a denormalised snapshot. Until that lands, a reopened draft degrades honestly: refine says "originality not checked — this draft has no source post" and the reference button is disabled with a reason. It does not silently pretend.

**Autosave — 5 minutes, and only when something changed.**

```
explicit Save draft   ─┐
switching tab away    ─┼─►  saveDraft(tab)   — always the whole post, one PUT
5-minute idle timer   ─┤     skipped entirely when serialize() == lastSaved
closing the browser   ─┘
```

Every path goes through one `saveDraft(tab)` that writes the **complete** post in a single request — composed body+tags, image, schedule — never a field at a time. That is what keeps a draft atomic: there is no window in which a row holds half of one edit and half of another.

Dirty is computed, not guessed: `dirty = serialize() !== tab.lastSaved`. That makes the 5-minute timer a no-op when you are reading rather than typing, which is most of the time — no request, no write, no cost. Combined with save-on-switch, the timer only ever matters when you sit on one tab for five minutes straight.

`PostCreate.content` has `min_length=1` (`schemas/post.py:8`), so a save waits for non-empty content rather than erroring quietly.

**Closing the browser tab.** You asked for a popup offering to save. Worth being straight about what browsers permit:

- **A custom dialog is not possible.** `beforeunload` lets a page *request* a confirmation, but the browser shows its own wording ("Leave site? Changes you made may not be saved"), ignores any text you supply, and only fires at all if you have interacted with the page. There is no API for "here is my Save button".
- **Actually saving on the way out is possible**, and is the better outcome anyway: `fetch(url, {method: 'PUT', keepalive: true})` survives the page closing (64KB cap — a 3000-char post is nowhere near it).

So the design is: fire the save, *and* raise the browser's confirm as a backstop —

```
pagehide / visibilitychange → any dirty tab?  → keepalive PUT for each
beforeunload                → any dirty tab?  → preventDefault()  (browser's own dialog)
```

The save is the real protection; the browser dialog is a second chance if the save fails. A tab that has never been saved and has content gets a keepalive **POST** to create it — losing typed work to a stray Cmd-W is worse than an extra row, and you asked for no limit on drafts.

**History stops showing drafts.** `loadHistory` currently lists every post and badges the unpublished ones "Draft". With a library panel that becomes the same rows in two places, so History filters to `published | scheduled | publishing | failed` and drafts live only in the panel. One home each.

### 3.5 Multiple open drafts (E) — a document model, not a bigger form

This is the largest change in the plan, and it is worth being explicit about why: today Create Post is a **singleton editor**. There is one draft, and its state *is* the DOM — `getPostState()` reads it back out of the inputs. Tabs mean N drafts held at once, and only one of them can be in the DOM.

Two ways to do it, and only one is available:

| | |
|---|---|
| **N copies of the form, show one** | Clean isolation — but `app.js` performs 113 `getElementById` / `querySelector` lookups against unique ids. Duplicating the form duplicates the ids and every one of those lookups grabs the wrong copy. **Not available** without finishing the component refactor first |
| **One form, swap state on switch** | The DOM stays unique; each tab holds a serialized state object, hydrated on activate. **This is the plan** |

```
  tabs: [ {id, draftId, state, dirty}, … ]        activeTab
                     │                                 │
      switch away ───┤ serialize()  DOM → state        │
      switch to    ──┴ hydrate()    state → DOM  ──────┘
```

**The risk this creates, stated plainly: state bleed.** If any piece of editor state is missing from `serialize()`, it does not get captured on switch-away and does not get overwritten on switch-to — so it **survives across tabs**. Edit draft A with an image, switch to draft B, and B silently carries A's image. Publish and you have shipped the wrong picture. Nothing throws; it just looks like it worked.

The defence is structural, not vigilance:

1. **One function defines the state.** `getPostState()` already exists as "what is this post right now". It becomes the serializer, extended to cover everything a tab owns — body, tags, image, schedule, active section, exemplar context, refine history, `draftId`, dirty flag.
2. **`hydrate(state)` is its exact inverse**, and lives next to it.
3. **A round-trip test proves they match**: fill every field → `serialize()` → clear the form → `hydrate()` → `serialize()` again → deep-equal. A forgotten field fails this mechanically instead of being spotted in review.
4. **Switching saves.** Leaving a tab commits it, so background tabs are never dirty and never at risk. Autosave then only ever concerns the active tab.

Everything else follows:

- **Unsaved marker** (`•`) on the tab — with several open you cannot see the others, so dirty state has to be visible on the tab itself.
- **Closing a dirty tab confirms** via the existing `<confirm-modal>`.
- **A new tab has `draftId: null`** until its first save assigns one.
- **Publishing closes its tab** and removes the draft from the library — it is a published post now, and it belongs to History.
- **Open tabs survive a reload** — store the open `draftId`s in `localStorage`; unsaved never-saved tabs cannot survive and should not pretend to.
- **No cap on open tabs** — the bar scrolls horizontally. (I had proposed evicting the oldest clean tab at 8; you asked for no limits, and silently closing something a user opened is the wrong trade even when it is safely saved.)

### 3.6 Bug register

Five, found by checking the code rather than waiting for them. Three exist **today** and are latent only because nothing reopens a draft; two are hazards this work would create.

| | Bug | Where | Kind | Fixed in |
|---|---|---|---|---|
| ① | `update_draft` overwrites a **published** post | `post_service.py` — no status check | Live, silent data corruption | **B0** |
| ② | An image or schedule can never be **cleared** | `post_service.py` — `if x is not None` | Live, silent no-op | **B0** |
| ⑤ | `posts.image_source` is **never written** | schemas + service don't carry it | Live, dead column | **B0** |
| ③ | Autosave can resurrect a published post | new — timer races publish | Hazard | R4 (① is the backstop) |
| ④ | Sidebar toggle would beat the media query | new — inline styles vs CSS | Hazard | R4 |

①②⑤ are backend-only, independent of every phase, and touch nothing else — so they ship first as **B0**, before any UI work depends on them being right.

**① `update_draft` will overwrite a published post.** There is no status check — `post_service.py` fetches by id, verifies ownership, and writes. A 5-minute timer or a keepalive save that lands *after* publish rewrites the `content` of a live post, so your History would show text that differs from what is actually on LinkedIn, with nothing indicating it.

> **Fix (server, one guard):** reject the update when status is `published` or `publishing` → 409. Client-side cancellation of pending saves is *also* needed, but the server guard is what makes it impossible rather than unlikely. Two lines, no behaviour change for any existing caller.

**② An image can never be removed from a draft.** `update_draft` treats `image_url=None` as "leave it alone" (`if image_url is not None`). Remove the image, save, reopen — the old image is still there. Same for `scheduled_time`: a scheduled draft cannot be un-scheduled.

> **Fix:** distinguish "not sent" from "sent as null" using Pydantic's `exclude_unset`, which `PostUpdate` already supports for free — `body.model_dump(exclude_unset=True)`. Callers that omit a field keep today's behaviour exactly; callers that send `null` can now clear. No new endpoint, no schema change, nothing else affected.

**③ Autosave could resurrect a published post as a draft.** Publishing closes the tab (§3.5), but a timer already in flight can still fire. Cancel pending saves and clear the tab's dirty flag *before* the publish request, not after — plus guard ① as the backstop.

**⑤ `posts.image_source` is a dead column — it is never written.** The migration added it (`e25708ce582b`), `media_service` produces the value and returns it as `StoredImage.image_source` (`:181-182`), and `POST /media/*` hands it to the client. Then it stops: `PostCreate` / `PostUpdate` do not carry it, `create_draft` / `update_draft` do not accept it, so the column is **NULL on every row**. The frontend receives the provenance and discards it.

That matters for saving the image with a draft: reopening should tell you *how* this image got here — AI-generated, uploaded from your machine, or fetched from a URL — because it changes what you would do next (regenerate vs re-upload). The value already exists at every call site; nothing carries it the last step.

> **Fix:** add `image_source` to `PostCreate` / `PostUpdate` and pass it through both service methods, and stop discarding it in `applyImage()`. Additive and optional — existing callers that omit it write NULL exactly as they do today, so nothing else is affected.

**④ The manual sidebar toggle must not fight the responsive rule.** `style.css:1118` already hides the sidebar below 1024px. A collapse toggle that sets inline styles would win over that media query and reintroduce the sidebar on a phone.

> **Fix:** toggle a class on `.app-layout` and let CSS decide, rather than writing inline styles from JS. The media query keeps precedence at small widths.

### 3.7 Collapsible sidebar

Clicking the brand/logo collapses the 280px nav to a 64px icon rail, giving the editor that width back — which matters most now that Create Post has four columns.

```
expanded   │ 280px sidebar │ 240 library │ editor │ 300 rail │
collapsed  │ 64 │ 240 library │ ────── editor +216px ────── │ 300 rail │
```

Class on `.app-layout`, `grid-template-columns` swapped in CSS, state in `localStorage` so it persists. Nav items keep their icons and gain `title` tooltips when collapsed. No JS inline styles (bug ④).

---

## 4. Changes

```
Backend
  hashtag_service.derive_hashtags(text, count)          NEW — tags from your own prose
  content_generation_service.refine_post(...)           NEW — instruction-driven rewrite
  POST /generate/hashtags   {text?, exemplar_id?, count}  NEW
  POST /generate/refine     {text, instruction, exemplar_id?}  NEW
  GET  /posts/?status=draft                               NEW filter (list route exists)
  post_service.update_draft                               FIX — reject published/publishing (bug ①)
  posts.update_post                                       FIX — exclude_unset, so null clears (bug ②)
  PostCreate / PostUpdate + create_draft / update_draft   FIX — carry image_source (bug ⑤)

Frontend
  <hashtag-editor>        NEW component — chips, add, remove, two generate buttons
  <refine-box>            NEW component — instruction input, recent instructions
  <draft-library>         NEW component — left panel, open/delete, collapsible
  <draft-tabs>            NEW component — open documents, dirty markers, close
  create-sections         + hashtags section (4 -> 5 sections), 4-column layout
  create-rail             + hashtag status; drafting state during handoff
  app.js                  body/tags split, composeFullText(), decomposeFullText(),
                          serialize() / hydrate(), tab store, staged handoff,
                          retain exemplar context, draftId + autosave,
                          pagehide keepalive save + beforeunload backstop
  loadHistory             filter out drafts (§3.4)
  style.css / app.js      sidebar collapse via a class, never inline styles (§3.7)
```

No schema change. No migration. Draft CRUD is reused as-is (§2).

---

## 5. Phases

### B0 — Bug fixes ①②⑤ *(backend only, no UI)*
`ConflictException` (409). Status guard on `update_draft`. `UNSET` sentinel so `null` clears and omission does not. `image_source` carried through `PostCreate` / `PostUpdate` / `PostResponse` / both service methods.

**Verify:** publish then update → 409, content unchanged · send `image_url: null` → cleared · omit it → untouched · send `scheduled_time: null` → un-scheduled, status back to `draft` · create with `image_source` → persisted and returned.

### R1 — Staged handoff (A)
Switch tab on click; drafting skeleton over Post Body; two-call staging; same for `Find & draft automatically`; retain `exemplar_id` / `exemplar_url` / `exemplar_author` in the Create view and show the attribution.

**Verify:** click Draft one like this → Create Post is visible within ~100ms with a drafting state, body appears before the image, and an image failure leaves an editable draft plus a note rather than an error.

### R2 — Hashtag section (B)
Body/tags split, `composeFullText()`, chip editor, `derive_hashtags()`, wire both generate buttons, char counter and preview onto composed text.

**Verify:** remix a post → chips populate from `result.hashtags` and the body has no trailing tag block; edit a chip → the preview and char count follow; publish → the DB row contains body + tags exactly as previewed; "From the reference" is disabled with a reason on an exemplar-less draft.

### R3 — Refine prompt (C)
`refine_post()`, the endpoint, the `<refine-box>` component, similarity re-check on every refine, honest "not checked" state when there is no exemplar.

**Verify:** refine three times in a row → similarity is recomputed and displayed each time, not carried over; ask for a shape change ("make it two paragraphs") → shape is unchanged and the UI points at the paragraph control; a refine that trips the gate is refused with the reason, not silently applied.

### R4 — Draft library, one document at a time (D)
`<draft-library>` in a fourth column, collapsible below 1280px. `draftId` threaded through save, autosave and **publish**. `?status=draft` filter. Decompose body/tags on open. History filtered to exclude drafts. **`serialize()` / `hydrate()` built here** — R5 needs them and this is the phase where they can be proven against a single document.

**Verify — the first two are the ones that matter:**
1. Open a draft, edit, publish → **the same row is published**; no orphan draft is left behind and no duplicate row appears.
2. Save the same draft five times → **one row**, updated five times.
3. Save a draft with hashtags and an image, reload the page, reopen it → chips, body and image come back separated exactly as they were.
4. Reopen a draft with no lineage → refine reports "not checked", the reference-hashtag button is disabled with a reason, and neither silently no-ops.
5. Type, close the browser tab immediately, reopen → the work is there (the keepalive save, not the timer).
6. History shows no drafts; the library shows only drafts.
7. **Round-trip**: fill every field → `serialize()` → clear → `hydrate()` → `serialize()` → deep-equal.
8. **Bug ①** — publish a post, then force a save against its id → **409**, content unchanged.
9. **Bug ②** — remove a draft's image, save, reopen → the image is gone, not resurrected.
9b. **Image round-trip, all three sources** — save a draft with an AI-generated image, one uploaded from disk, and one fetched from a URL; reopen each → the image renders and **`image_source` reports how it got there** (bug ⑤), rather than NULL.
10. Sit on an unchanged draft for 6 minutes → **zero requests** (dirty-gated timer).
11. Sidebar collapse: toggle, reload → still collapsed; then narrow to 900px → the media query still hides it (bug ④).

### R5 — Multiple open drafts (E)
`<draft-tabs>`, the tab store, save-on-switch, dirty markers, close-confirm, tab restore from `localStorage`.

**Verify — every one of these is a state-bleed probe:**
1. Open A with an image, open B with none, switch A→B→A → **B never shows A's image**, and A still has it.
2. Same for schedule, hashtags, active section, refine history and exemplar attribution — one assertion each, because each is a field that can be forgotten in `serialize()`.
3. Switch away from a dirty tab and back → no data loss and no confirm prompt (switching saves).
4. Close a dirty tab → confirms; close a clean one → does not.
5. Publish from tab 2 of 3 → that tab closes, the other two are untouched, and the draft leaves the library.
6. Reload with three tabs open → three tabs return.

---

## 6. Risks

1. **The body/tags split touches the publish path.** `getPostState().content` feeds create-post, the preview and (later) lineage. Composing in one helper and using it everywhere is what keeps preview and published text identical — the alternative is two places drifting apart.
2. **Refine without a similarity re-check is a slow leak** (§3.3). The gate must run every time, against the original exemplar.
3. **Refine can quietly become a hashtag generator.** Models like ending posts with tags. `strip_trailing_hashtag_block()` on every refine output.
4. **Staged handoff makes partial state normal.** A draft with body-but-no-image is now a state you can see and act on. Publish must work from it, and the rail must not claim an image that is still generating.
5. **Both this plan and the discovery plan rebuild the Create Post view** (§7).
6. **`derive_hashtags` is a new Gemini call site** — same trap as everywhere else: `ai_service.py:54-56` returns canned marketing copy when Gemini is down. Guard with `is_template_fallback`, or `#Excited` becomes a hashtag.
7. **The orphan-draft bug is created by this work, not found by it** (§3.4). `handlePostSubmit` creating a new row unconditionally is harmless today only because nothing reopens drafts. R4 must thread `currentDraftId` through publish in the same change that adds the panel — never after.
8. **Autosave amplifies whatever `draftId` gets wrong.** A bug that creates one extra row per save becomes one every five minutes, and once per browser close. Ship explicit save first, autosave second, once round-tripping is proven.
9. **Four columns is a lot of chrome.** At 1440px the editor gets ~560px. If that reads cramped in practice, the library panel is the one to make collapse-by-default rather than shrinking the editor further.
10. **State bleed between tabs is the highest-severity risk in either plan** (§3.5). It corrupts data, produces no error, and looks like success — you would find it when the wrong image reached LinkedIn. `serialize()`/`hydrate()` as inverses plus the round-trip test is the mitigation; vigilance is not.
11. **R5 raises the cost of every future editor field.** After tabs, adding a control to Create Post means adding it to `serialize()` and `hydrate()` too. Anyone who forgets reintroduces risk 10. The round-trip test is what makes that a failing test rather than a silent bug — it has to assert over the full field set, not a sample.

---

## 7. Sequencing against the other plan

The discovery plan's **P4** replaces the Create Post exemplar picker and adds the paragraph-count control. This plan's **R2** and **R3** add sections to the same view and depend on exemplar context being present.

Recommended order: **B0 → P4 → R1 → R2 → R4 → R3 → R5.** B0 depends on nothing and is a prerequisite for R4 being correct, so it goes first regardless of what else is scheduled. R1 alone could land earlier — it only changes when the tab switch happens and what the remix response is used for.

R4 sits after R2 because a draft stores *composed* text: build the split first and the round-trip is `composeFullText()` / `decomposeFullText()` on a settled format. Build R4 first and you save pre-split drafts, then have to migrate how they reopen.

**R5 goes last, deliberately.** It depends on `serialize()`/`hydrate()` covering the *complete* field set, and every phase before it adds fields — hashtags in R2, refine history in R3. Building tabs first means extending the serializer three more times, and each extension is a chance to reintroduce state bleed. Building it last means the field set is settled and one round-trip test locks it.

R4 also reads better after the discovery plan's **P5** (`draft_lineage`), which is what lets a reopened draft remember its exemplar. It works before P5 — it just degrades honestly instead (§3.4).

Doing R2/R3 before P4 means building against a picker that is about to change.

---

## 8. Decisions

| | Question | Settled |
|---|---|---|
| 1 | Hashtag count | **Match the exemplar's count** when there is one, 5 otherwise — which is `remix_hashtags`' existing behaviour with `count` omitted |
| 2 | Refine history | **Last 5 versions, in memory, session only.** No persistence |
| 3 | Recent instructions | **Session only** |
| 4 | Does refine touch hashtags? | **No** — body only; tags are edited in their own section |
| 5 | History vs drafts | **Separate.** History shows published / scheduled / publishing / failed; drafts live only in the library |
| 6 | Autosave interval | **5 minutes idle, and only when dirty.** Save-on-switch and the on-close keepalive save cover the gap the longer interval opens |
| 7 | Draft limit / expiry | **None.** Drafts are your work and are never auto-removed |
| 8 | Open-tab limit | **None** — the bar scrolls (§3.5) |
| 9 | Save prompt on browser close | **Save, don't prompt.** A custom dialog is not possible; a `keepalive` PUT is, and it is the better outcome. The browser's own confirm is kept as a backstop (§3.4) |
| 10 | Sidebar | **Collapsible to a 64px icon rail**, class-toggled, persisted (§3.7) |

Nothing outstanding. This plan is ready to execute once the discovery plan's P4 lands, or R1 can start immediately if you want something visible sooner.
