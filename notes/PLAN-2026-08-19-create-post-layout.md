# Plan — Create Post layout, field order, and the post-generation landing

Date: 2026-08-19
Branch: `jul-9-contentGeneration-fix-branch`
Status: **PROPOSED — not started**
Trigger: screenshot review of the AI assistant panel after P4 shipped

---

## 1. What was asked

1. Topic / Core Idea + **Generate Draft** should sit directly below **Post type**
2. **Your Specific Examples / Notes** should move to the bottom
3. Clicking Generate Draft should land you on the generated post
4. The draft area inside Create Post should be more left-aligned
5. The middle section should be bigger

---

## 2. What was measured before planning

Item 3 was checked rather than assumed, by booting `app.js` under the test DOM stub and
observing which tab and section each generate path ends on.

| Path | `switchTab` | active section | launcher |
|---|---|---|---|
| Discovery card → "Draft one like this" | `create` | `body` | hidden |
| Create Post → Generate Draft, **exemplar selected** | `create` | `body` | hidden |
| Create Post → Generate Draft, **no exemplar** | *(none)* | **`ai`** | **visible** |

**The redirect from Discovery already works.** Both exemplar paths run through
`runStagedHandoff` → `openHandoff()` (`app.js:573-578`), which switches tab, resets the
editor and calls `showSection('body')`.

The path that does not redirect is the **plain** one — no exemplar selected. It writes the
generated text straight into the textarea (`app.js:~880`) and never calls `showEditor()` or
`showSection('body')`, so you are left on the AI form while the draft sits in a body section
you were not moved to. That is the real defect behind the request; the request located it in
the wrong place, but the symptom is genuine.

Probe kept at `scratch/probe_redirect.mjs` — rerun it after the change.

---

## 3. Current field order vs requested

`index.html:368-444`, inside `.ai-assist-body`.

| | Now | Proposed |
|---|---|---|
| 1 | `<exemplar-picker>` | `<exemplar-picker>` |
| 2 | Post type | Post type |
| 3 | **Notes** | **Topic / Core Idea + Generate Draft** |
| 4 | Paragraphs | Paragraphs |
| 5 | Hook / Rhythm / Vocabulary | Hook / Rhythm / Vocabulary |
| 6 | **Topic + Generate Draft** | **Notes** |

### One concern worth stating before doing it

This puts the **action above the controls that feed it**. After the change, Paragraphs, Hook,
Rhythm, Vocabulary and Notes all sit *below* the Generate Draft button — so a user who types a
topic and presses the button immediately never sees the options that shape the draft, and the
defaults silently decide. Today those controls are passed on every generate
(`app.js:~885-895`), so this is a discoverability loss, not a functional one.

Two mitigations, both cheap; **the choice is the user's** and the plan proceeds either way:

- **A.** Ship the order exactly as asked, and add one line of help text under the button:
  *"Paragraphs, tone and notes below shape the draft."*
- **B.** Ship the order as asked, and group items 4–6 under a collapsed
  **"Fine-tune"** disclosure directly beneath the button, so the panel reads shorter and the
  options are visibly *available* rather than scrolled past.

Default assumed if unanswered: **A** — smaller change, no new component, no new state to
round-trip through `serialize()`/`hydrate()`.

---

## 4. Layout — where the width actually comes from

`.create-workspace` is defined **twice**, and the second wins for columns:

- `style.css:1442-1452` — `grid-template-columns: minmax(0, 1fr) 300px`, `max-width: 1280px`, `margin: 0 auto`
- `style.css:1920-1925` — overrides to `240px minmax(0, 1fr) 300px`, and `.library-hidden` back to two
- `style.css:2077-2083` — under 1280px, collapses to two columns and hides the library

So the middle column today is `1280 − 240 − 300 − (2 × 28 gap)` ≈ **384px** on a wide screen,
which matches the cramped panel in the screenshot. Risk 9 of the draft plan predicted exactly
this ("four columns is a lot of chrome… ~560px at 1440px").

**Change, at `style.css:1442-1452` only** — the second block sets columns, not width, so
editing width there would be the wrong place:

```css
.create-workspace {
    max-width: 1600px;      /* was 1280px */
    margin: 0 auto 0 0;     /* was 0 auto — left-aligns instead of centring */
}
```

At 1600px the middle column becomes ≈ **704px**, an 83% increase, without touching the rail
or the library. Left-aligning removes the dead gutter on the left that the screenshot shows.

**Watch:** the `max-width: 1280px` media query at `:2077` is a *viewport* breakpoint, not a
container one, so raising the container max-width does not move it. Below 1280px viewport the
library still hides and the layout still drops to two columns — unchanged behaviour.

**Ambiguity flagged:** "the draft section … more left aligned" could mean the whole workspace
(read here) or the `draft-library` panel specifically. This plan left-aligns the workspace,
which is what the screenshot's centred layout suggests. Say if the library was meant.

---

## 5. Commits

Small and separable, as with P4/P7.

| # | Commit | Touches | Verify |
|---|---|---|---|
| 1 | Land on the draft after a plain generation | `app.js` | Rerun the probe: plain path reports `section=body`, launcher hidden. Add a JS test beside `test_exemplar_picker.mjs` asserting all three paths land on `body` |
| 2 | Reorder the assistant fields | `index.html` | Field set unchanged, so `serialize()`/`hydrate()` and the pinned field-set assertion must both still pass untouched — that is the regression guard |
| 3 | Widen and left-align the workspace | `style.css` | Visual; confirm no horizontal page scroll at 1280/1440/1920 and that `minmax(0, 1fr)` still prevents the textarea pushing the track |

Commit 1 is the only behavioural change and is independent of 2 and 3 — it can ship first.

---

## 6. What must not break

- **`minmax(0, 1fr)` stays.** `style.css:1444-1446` documents why: a grid child defaults to
  `min-width: auto`, so a long URL in the textarea would push the track and scroll the whole
  page sideways. Widening the container does not remove that hazard.
- **No new form field.** Reordering moves existing nodes; it must not introduce a control,
  because every editor field has to join `serialize()`/`hydrate()` and the pinned field-set
  test — the cost R5 will inherit (draft plan, risk 11).
- **`novalidate` stays** on the form (`index.html:~358`): a hidden section's `required` field
  would otherwise block submit with no visible error.
- **The plain path stays.** Discovery can legitimately have found nothing, so generating
  without an exemplar has to keep working — commit 1 changes where it lands, not whether it runs.

---

## 7. Open questions

1. Mitigation **A** or **B** for the action-above-controls concern? (default: A)
2. "More left aligned" — the whole workspace, or the draft library panel?
3. Is 1600px the right ceiling, or should the workspace be fluid to the viewport?
