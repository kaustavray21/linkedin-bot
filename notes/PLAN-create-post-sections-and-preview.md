# Plan — Sectioned Create-Post Workspace + Pre-Publish Preview

Date: 2026-08-15 (rev 4 — executed)
Branch: `jul-9-contentGeneration-fix-branch`
Status: **IMPLEMENTED — Phases 1–4 built and verified.**

---

## EXECUTION LOG (2026-08-15)

All four phases built. Verified by driving the real markup in headless Chrome
against a stubbed API, so no request could reach LinkedIn. **52/52 checks pass.**

| Claim | How it was checked | Result |
|---|---|---|
| Only one section shows at a time | Counted non-`hidden` panels after each switch | **1 of 4**, correct one every time |
| Rail navigation and Back/Next agree | Clicked rail entries and both nav buttons | Section and highlight follow, Next disabled on the last |
| Rail status is live | Typed into the textarea, set and cleared an image | `27 chars` / thumbnail / `none`, updating on every change |
| Preview reproduces line rhythm | Compared `.preview-content` text to the textarea, byte for byte | **Exact match**, computed `white-space: pre-wrap` |
| Cancelling sends nothing | Recorded every API call; cancelled via Escape and Back-to-editing | **Zero calls** in all cancel paths |
| Confirming publishes | Clicked confirm | `createPost` then `publishPost`, content matches the draft |
| Validation blocks before the network | Submitted empty, and at 3100 chars | No modal, zero calls, jumped to the body section |
| Both clocks shown when scheduling | Read the timing line | `Scheduled for Wed, Mar 3, 9:00 AM (your time) — 03:30 UTC` |
| Inline `onclick="app.…"` still resolves | Asserted `window.app` exists | Present — the §7.1 regression check |
| History confirms use the shared modal | Ran delete and publish-from-history | Modal opens, delete styled destructive, cancel calls nothing |
| Toast cannot execute injected markup | Passed `<img src=x onerror=…>` as a message | Rendered as literal text, no `<img>` node, handler never ran |
| One `escapeHtml`, and it survives a number | Called `app.escapeHtml(42)` and with quotes | `'42'`, quotes escaped, single definition |

### Two bugs the verification caught

1. **The rail had no highlight on first paint.** `components/index.js` imports
   `create-sections` before `create-rail`, so `create-sections` is defined and
   upgraded *first*. Its opening `show('ai')` ran while `<create-rail>` was still
   an unupgraded unknown element with no `setActive` method, and the call was
   silently skipped by the guard. Fixed by syncing from both sides: the parent
   pushes on every `show()`, and the rail *pulls* `owner.active` when it mounts.
   Either order now works. This is §7.4 (deferred-module ordering) turning up in
   a place the plan hadn't predicted — the ordering hazard was real, the specific
   mechanism was not the one anticipated.

2. **Mobile rail showed only one of four entries.** In the ≤1024px horizontal
   strip, `.rail-item` inherited `width: 100%` from its base rule, so entry one
   filled the strip and entries two through four were pushed out of the scroll
   view. Found by screenshot, not by assertion — the DOM was correct, only the
   layout was wrong. Fixed with `width: auto` in the media query.

3. **The delete dialog said "Back to editing".** Phase 4 reused the modal for
   History's confirmations, but the cancel label was hardcoded for the publish
   preview — so deleting a post offered to take you "back to editing" something
   you were not editing. Found by screenshot; every assertion passed while the
   wording was wrong. Cancel label is now a parameter, defaulting to `Cancel`.

The first two were silent failures: nothing threw, nothing logged, and the DOM
read as correct in each case. The third was invisible to assertions entirely —
only looking at it caught it.

### Deviations from the plan as written

- `app.js` no longer listens for `section-shown` to update the rail highlight —
  `<create-sections>` owns that directly, for the ordering reason above. The
  event still fires as a public signal; nothing consumes it yet.
- Added: `applyRemixResult` now lands on the **Post Body** section, since that is
  where an auto-generated draft has just arrived. Not in the plan, one line,
  clearly right.
- Added: the publish button relabels to `Review & Publish` / `Review & Schedule`
  rather than `Publish Immediately`, because it no longer publishes directly.
- Added to the submit reset: `#datetime-picker-container` is re-hidden. It had
  the same stale-state bug as the variations picker.
- Phase 4 added `app.confirmAction()` rather than calling the modal directly from
  both History actions. It owns one fallback to `window.confirm()` for the case
  where the component module fails to load — losing the confirmation gate on a
  publish or a delete would be worse than an ugly dialog.
- Phase 4's `escapeHtml` cleanup went slightly further than "delete the dead
  one": the surviving copy now coerces with `String()` first. The deleted one
  handled non-strings; the survivor would have thrown on a number. Kept the
  stricter escaping and the safer coercion rather than one or the other.

---

## 1. What's being asked

Three changes, all in the Create Post view:

1. The centre column is too long for one component — split it into sections.
2. Use the empty black area on the right to select which section you're editing.
3. Before publishing or scheduling, show a preview popup of the post's details
   and content.

## 2. Decisions already made

| Decision | Choice | Why |
|---|---|---|
| Section navigation | **Swap** — one section visible at a time | Scroll-to-anchor leaves the column exactly as long as it is now; swapping is what actually shortens it |
| Architecture | **Component the new parts only** | The three new pieces are built as self-contained components; `app.js`, `api.js` and the other three views are untouched. See §4 |
| Component tech | **Custom elements, light DOM, no build step** | No `package.json` exists; FastAPI serves `index.html` off disk (`main.py:114`). Light DOM (no shadow root) so the existing `style.css` and CSS variables still apply |

## 3. What's there now

`#view-create` (`index.html:218-481`) is one `<form>` holding four stacked
`.form-section` blocks in a single 800px column:

| # | Section | Markup | Roughly |
|---|---|---|---|
| 1 | Draft with AI Assistant | `index.html:223-349` | **127 lines** — style select, reference-post list, style analysis, notes, 6 dropdowns, topic input |
| 2 | Review & Edit Post Body | `index.html:352-374` | 23 lines — textarea, char counter, variations picker |
| 3 | Add an Image | `index.html:377-444` | 68 lines — 3 source tabs, 3 panels, preview |
| 4 | Publish or Schedule | `index.html:447-469` | 23 lines — now/later toggle, datetime picker |

Section 1 alone is over half the form and, as the screenshot shows, section 2
starts below the fold. The width is fixed by `.wizard-container { max-width:
800px; margin: 0 auto }` (`style.css:669-672`) inside `.content-body` (40px
padding) inside a `280px 1fr` layout grid (`style.css:348-352`). On a wide
monitor the `1fr` track is ~1600px, so ~400px sits empty on each side — that is
the black area in the screenshot.

Publishing today goes straight through: `form submit` → `handlePostSubmit()`
(`app.js:663-710`) → `API.createPost()` → `API.publishPost()`. **No confirmation
of any kind.** There is no modal component anywhere in the codebase; History
uses the browser's native `confirm()` (`app.js:840`, `app.js:856`).

**Nothing here touches the backend.** No new endpoints, no schema changes, no
service changes, no Python file.

## 4. The component convention

### 4.1 What gets componentised, and what deliberately does not

This is the line that keeps this from turning into a rewrite:

| Piece | Treatment | Why |
|---|---|---|
| `<confirm-modal>` | **Full component** — owns its markup, renders freely | Entirely new markup. Nothing external holds a reference into it |
| `<create-rail>` | **Full component** — owns its markup, re-renders on state change | Entirely new markup |
| `<create-sections>` | **Controller only** — toggles `hidden`, never re-renders | The four panels are existing markup that `app.js` binds to by id. Re-rendering them would destroy elements that already have listeners attached |

That last row matters. `app.js` performs **113** DOM lookups by string id
(95 `getElementById`, 18 `querySelector`). Every one of them points into markup
that must survive. A component that re-rendered the four panels would silently
detach every listener bound in `setupEventListeners()` — the form would look
right and do nothing. So `<create-sections>` orchestrates visibility and touches
nothing inside the panels.

### 4.2 Files

```
app/static/js/
├── api.js                      # UNTOUCHED, stays a classic script
├── app.js                      # existing controller, stays a classic script
└── components/
    ├── base.js                 # ~50 lines: Component base + safe html`` helper
    ├── confirm-modal.js        # <confirm-modal>
    ├── create-rail.js          # <create-rail>
    ├── create-sections.js      # <create-sections>
    └── index.js                # imports the three, nothing else
```

`index.html` gains exactly one line:

```html
<script src="/static/js/api.js"></script>
<script src="/static/js/app.js"></script>
<script type="module" src="/static/js/components/index.js"></script>   <!-- new -->
```

### 4.3 `base.js`

Two things, both small:

```js
export const html = (strings, ...values) => /* escapes every interpolation */
export class Component extends HTMLElement { /* state, setState, render, $, on */ }
```

The `html` tag escapes interpolated values by default. This is worth having for
its own sake: `app.js` currently has **two** different `escapeHtml`
implementations (`app.js:310-314` and `app.js:899-906` — the second silently
wins, the first is dead code), and `showToast` (`app.js:885-888`) interpolates
`message` into `innerHTML` **unescaped** while every other render path escapes.
A tagged template makes that mistake unrepresentable in the new code.

`Component` extends `HTMLElement` with no shadow root, so `.btn`, `.card`,
`--bg-primary` and the rest of `style.css` apply normally inside components.

### 4.4 Talking to `app`

Components need `app` for `showToast` and post state. `app.js` gains **one line**
at the end:

```js
window.app = app;          // explicit global, for components and inline handlers
```

This is additive and makes the five existing inline `onclick="app.…"` handlers
more robust rather than less. See §7.1 for why the alternative — converting
`app.js` itself to a module — is off the table.

Data flows one way: `app` owns state, components render it and emit events.

| Direction | Mechanism |
|---|---|
| app → component | `railEl.update(state)` / `modalEl.open(opts)` |
| component → app | `CustomEvent` (`section-change`, `request-publish`) |

## 5. Phases

### Phase 1 — `base.js` + two-column shell + `<create-sections>`

**`components/base.js`** — the `html` tag and `Component` base. Nothing else.

**`index.html`**
- Wrap `#view-create`'s content in `<create-sections class="create-workspace">`;
  the existing `<form>` goes in `.create-main`, `<create-rail>` beside it.
- Give each of the four `.form-section` blocks `data-section="ai|body|image|schedule"`.
  **Markup inside them is untouched.**
- `[← Back] [Next →]` in `.create-main`.
- Move the submit button (`index.html:472-477`) into the rail as *Review & Publish*,
  so publishing is reachable from any section.
- Add `novalidate` to the form — see §7.2, this is load-bearing.
- Add the one new `<script type="module">` line.

**`style.css`**
- `.create-workspace { display: grid; grid-template-columns: minmax(0,1fr) 300px;
  gap: 28px; max-width: 1280px; margin: 0 auto; align-items: start; }`
  `minmax(0,1fr)` not `1fr` — see §7.5.
- `create-sections, create-rail, confirm-modal { display: block }` — custom
  elements are `display: inline` by default, which quietly breaks grid children.
- `.create-rail` sticky (`top: 24px`) so it stays put while a tall section scrolls.
- Under `@media (max-width: 1024px)` (where the sidebar already vanishes,
  `style.css:1118`): one column, rail becomes a horizontal strip above the form,
  drop `position: sticky`.
- `.wizard-container`'s 800px cap stops applying to Create; leave the rule, the
  style wizard uses it.

**`components/create-sections.js`** — `<create-sections>`:
- `show(name)` toggles `hidden` on the four panels, updates Back/Next, scrolls
  `.create-main` to top, emits `section-change`.
- Listens for `section-change` from the rail.
- **Never touches panel internals.**

**Verify:** all four rail entries show exactly one section; generate-draft still
fills the textarea; at 900px wide the rail sits above the form with no sideways
overflow; **check the console for zero errors** — a broken custom element upgrade
is silent otherwise.

### Phase 2 — `<create-rail>` with live status

`app.js` gains one method — the single source of truth for "what is the post
right now":

```js
getPostState() {
  return { profileSlug, selectedRefCount, topic,   // section 1
           content, charCount,                     // section 2
           imageUrl,                               // section 3
           scheduleType, scheduledLocal, scheduledUtc }; // section 4
}
```

`<create-rail>` renders four entries from it. Each carries a live status line —
that's what makes the rail worth its width instead of being a plain menu:

| Section | Status shows | Empty state |
|---|---|---|
| AI Assistant | `combined · 3 refs` | `no style` |
| Post Body | `412 chars` (red past 3000) | `empty` |
| Image | 32px thumbnail + `set` | `none` |
| Publish | `Publish now` / `Mar 3, 9:00 AM` | — |

`app.refreshRail()` calls `railEl.update(this.getPostState())` from the places
that already mutate state: the textarea `input` listener (`app.js:342`),
`applyImage()` (`app.js:641`), `clearGeneratedImage()` (`app.js:632`), the
schedule radios (`app.js:541`), the profile select (`app.js:486`), and
`handleSelectedPostsChange()` (`app.js:1031`).

The rail and the modal both read `getPostState()`, so they cannot disagree.

**Verify:** type in the textarea and watch the rail char count move; generate an
image and watch the thumbnail appear; switch to Schedule for Later, pick a time,
watch the rail show it.

### Phase 3 — `<confirm-modal>` + submit interception

**`components/confirm-modal.js`** — promise-based and reusable:

```js
modalEl.open({ title, body, confirmLabel, danger }) → Promise<boolean>
```

Resolves `false` on backdrop click, `×`, `Escape`, or Back-to-editing; `true` on
confirm. Restores focus to the trigger on close.

Preview body:

```
avatar + name (from this.user)
──────────────────────────────
post content        ← white-space: pre-wrap, NON-NEGOTIABLE
[image, if any]
──────────────────────────────
412 chars · image attached · Publish now
```

`pre-wrap` is the point of the whole feature for *this* app. Structure cloning
reproduces a creator's exact line rhythm (`layout_service.extract_skeleton`); a
preview that collapsed whitespace would show you something LinkedIn will not
show.

**`app.js`** — `handlePostSubmit()` restructured to:

1. Read `getPostState()`.
2. **Validate in JS** — replacing the `required` attribute removed in §7.2:
   empty body → toast + jump to section 2; over 3000 chars → toast + jump;
   `later` with no datetime → toast + jump to section 4.
3. `await modalEl.open(...)`. Return silently on `false`.
4. On `true` — the existing publish path (`app.js:679-709`) unchanged.
5. On success, also reset: `show('ai')`, hide `#variations-selector-container`,
   `refreshRail()` — see §7.3.

The timing line spells out both clocks:
`Scheduled for Mon 3 Mar 2026, 9:00 AM (your time) — 03:30 UTC`.
`handlePostSubmit` converts local→UTC (`app.js:676`) and the scheduler compares
against naive UTC. Showing both is what makes a wrong-timezone schedule visible
before it's committed rather than the next morning.

Warnings in the modal — informational unless noted:
- over 3000 chars → **blocks**, can't proceed
- no image → "This will publish as a text-only post."
- scheduled time in the past → "That time has already passed."

**Verify (direct observation, not asserted):**
1. Review & Publish with an empty body → toast, jumps to section 2, no modal,
   **zero network requests**.
2. Multi-paragraph draft with deliberate single-word lines → modal line breaks
   match the textarea **exactly**.
3. Generate an image → modal shows it.
4. Escape / backdrop / Back-to-editing → closes, **nothing published** (network
   tab shows zero requests).
5. Schedule for later → modal shows local + UTC → confirm → History row and DB
   `scheduled_time` agree with the UTC line shown.
6. Real publish → post appears on LinkedIn, form resets, rail resets to section 1,
   variations picker gone.
7. **Click all five existing `onclick="app.…"` buttons** (3× Create a Post, plus
   Publish Now and Delete in History) — they must still work. This is the §7.1
   regression check.

### Phase 4 — Optional, only if you want it

- Swap the two native `confirm()` calls (`app.js:840`, `app.js:856`) for
  `<confirm-modal>` — one confirmation style instead of two.
- Delete the dead `escapeHtml` at `app.js:310-314`.
- Route `showToast` through the `html` tag to close the unescaped-`innerHTML`
  gap at `app.js:885-888`.

## 6. What this buys beyond the three features

A working component pattern proven on the messiest view, with `app.js` and the
other three views untouched. If you like it, Discover / Dashboard / History
convert one at a time against a reference that already works. If you don't, the
blast radius is four new files you can delete.

What it explicitly does **not** do: rehome the 113 id lookups or the 66 inline
`style="…"` attributes in existing markup. That's the rewrite, and it stays out.

## 7. Risks — the things that will actually bite

**7.1 Module scope would break five existing buttons — silently.** There are five
inline `onclick="app.…"` handlers: `index.html:140`, `:156`, `:513` (Create a
Post) and two generated inside `loadHistory`'s `innerHTML` (`app.js:813`,
`app.js:819` — Publish Now, Delete). Inline handlers resolve identifiers against
the global scope. `app.js` is a classic script, so `const app = new App()`
(`app.js:1165`) lands in the global lexical environment and those handlers
resolve today. **If `app.js` were converted to `type="module"`, `app` becomes
module-scoped and all five throw `ReferenceError` at click time** — nothing fails
at load, nothing fails in the console until someone clicks. This is why `app.js`
and `api.js` stay classic scripts and only `components/` is a module, plus the
explicit `window.app = app` in §4.4. Phase 3 verification step 7 exists to catch
a regression here.

**7.2 Hidden `required` field silently kills submit.** `#post-text-content` has
`required` (`index.html:355`). Once section 2 can be hidden, submitting from
section 4 makes the browser try to focus an invisible required field and refuse:
*"An invalid form control with name='' is not focusable"* — console error, **no
submit, no user-visible feedback**. This is the single most likely way this ships
broken. Fix: `novalidate` **and** explicit JS validation. `novalidate` alone
removes validation entirely.

**7.3 `form.reset()` doesn't reset JS state.** `reset()` (`app.js:698`) clears
inputs but knows nothing about the active section, the variations picker, or the
rail. All three reset explicitly after a successful submit. (The stale-variations
bug is pre-existing — the picker is never hidden on submit today, so drafts
survive into the next post.)

**7.4 Module scripts are deferred; classic scripts are not.** `components/index.js`
executes after `app.js` but *before* `DOMContentLoaded`, so before
`app.onReady()` has wired listeners. Registering via `customElements.define()`
sidesteps this — the browser upgrades each element whenever it appears,
regardless of ordering. Components must not assume `app.onReady()` has run at
module-evaluation time.

**7.5 Grid overflow.** A grid child defaults to `min-width: auto`, so the textarea
and long unbroken URLs push the column past its track and force horizontal page
scroll. `minmax(0, 1fr)` on the main track is the fix and it's easy to forget.

**7.6 Custom elements are `display: inline` by default.** Unstyled, they break as
grid children in ways that look like a grid bug. Explicit `display: block` in
Phase 1.

**7.7 No shadow DOM, on purpose.** A shadow root would isolate components from
`style.css`, so `.btn` and `--bg-primary` would stop applying and every component
would need its styles duplicated. Light DOM keeps one stylesheet.

## 8. Scope boundary

**In:** `app/static/index.html`, `app/static/css/style.css`,
`app/static/js/app.js` (additive: `getPostState`, `refreshRail`, restructured
`handlePostSubmit`, `window.app = app`), and four new files under
`app/static/js/components/`.

**Out:** any backend file, any endpoint, the DB, `api.js`, the
Discover / Dashboard / History views (except the two optional `confirm()` swaps
in Phase 4), the LinkedIn publish path, and any rewrite of existing markup. No
Python test changes — verification here is visual and manual against the running
app.

## 9. Open questions

1. Should the modal render a **LinkedIn-shaped** preview (avatar, name, headline,
   "see more" fold at ~210 chars) rather than a plain card? Closer to the real
   thing, more CSS. Default: plain card with real line breaks.
2. Keep `[← Back] [Next →]` in the centre, or rail-only navigation?
   Default: keep both — Next is the natural path for a first run.
