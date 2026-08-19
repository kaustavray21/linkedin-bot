# Plan — two defects: the collapsed workspace layout, and NULLS LAST on MySQL

Date: 2026-08-19
Branch: `jul-9-contentGeneration-fix-branch`
Status: **PROPOSED — not started**
Trigger: screenshot of the collapsed Create Post view (`image copy.png`) + `error.log`

Two unrelated defects, found together. Both are diagnosed; neither is a hypothesis.

---

## Defect 1 — collapsing the draft library breaks the whole layout

### Observed

`image copy.png`: a wide empty bordered box holding a `»` icon fills the left ~1270px, and the
entire Create Post form is crushed into the ~260px right column — Hook Style, Rhythm and
Vocabulary are ~50px wide each. The rail is pushed below.

### Root cause — structural, not cosmetic

`.create-workspace` has **five direct grid children** (`index.html:334-628`):

| # | Child | Grid placement |
|---|---|---|
| 1 | `draft-library` | none — `display:none` when collapsed |
| 2 | `button.library-peek` | **none** |
| 3 | `div.create-launcher` | `grid-column: 2 / -1` (`style.css:2039-2044`) |
| 4 | `div.create-main` | none — auto |
| 5 | `create-rail` | none — auto |

Expanded, the grid is `220px minmax(0,1fr) 260px`: library → col 1, peek is hidden,
main → col 2, rail → col 3. Correct.

Collapsed, `.library-hidden` switches to two columns, `minmax(0,1fr) 260px` (`style.css:1948`)
— but the peek button becomes **visible** and, having no placement, claims the first cell.
Everything shifts one column: **peek → col 1 (the editor's 1fr), main → col 2 (260px), rail
wraps to the next row.** That is exactly the screenshot.

`.library-peek` (`style.css:2027-2035`) sets `align-self: start` and nothing else — it has never
been taken out of flow or placed.

### Provenance — pre-existing, but I made it worse

Introduced with the draft library in **`8931b64`**; `git show 8931b64:app/static/css/style.css`
shows the same unplaced `.library-peek`. So the broken collapsed layout has been latent since
then.

**What changed is the severity.** Commit `d11dc3d` (mine, today) persists the collapse in
`localStorage`. Before it, one reload cleared the mess; now it is sticky, and the user lands in
the broken layout on every visit until they find the `»` button. I turned a transient glitch
into a stuck state, and this plan fixes the underlying defect rather than reverting the memory.

### Fix

Give the collapsed grid a track for the button instead of letting it steal the editor's:

```css
.create-workspace.library-hidden {
    grid-template-columns: auto minmax(0, 1fr) 260px;   /* was minmax(0, 1fr) 260px */
}
```

`auto` sizes to the ~30px button. Column 2 stays the editor, column 3 stays the rail, and
`.create-launcher`'s `grid-column: 2 / -1` keeps working unchanged.

Below 1280px the media query already sets `.library-peek { display: none }` and two columns
(`style.css:2108-2111`), so that path is unaffected.

**Rejected:** `position: absolute` on the button. It would need `position: relative` on the
workspace and would overlay the editor's first line. Keeping it in flow with its own track is
both smaller and truer to what the layout means.

---

## Defect 2 — `/analytics/outcomes` 500s on MySQL

### Observed

`error.log`, one endpoint, one error, repeated once:

```
GET /analytics/outcomes?user_id=1  ->  500
pymysql.err.ProgrammingError (1064, "... right syntax to use near 'NULLS LAST' at line 3")
```

### Root cause

`NULLS LAST` is PostgreSQL syntax. MySQL has no such clause. Two call sites, both mine, both
from commit `15e8016`:

- `app/api/analytics.py:75`
- `app/services/outcome_service.py:180`

```python
.order_by(Post.published_time.desc().nullslast())
```

**The analytics panel has never worked against the real database.** It returns 500 every time
the dashboard loads.

### Why 268 passing tests did not catch it

`tests/conftest.py:36` runs every test against `sqlite+aiosqlite:///:memory:`. SQLite has
supported `NULLS LAST` since 3.30; MySQL never has. The suite is therefore blind to every
MySQL-specific SQL error by construction — it tests a different database than production runs.

This is the same shape as the parser-fixture failure found on 2026-08-18: a test that passes
because it is asked a question production never asks.

### Fix

Express the intent portably instead of reaching for a dialect keyword:

```python
.order_by(Post.published_time.is_(None), Post.published_time.desc())
```

`IS NULL` yields 0 for rows that have a date and 1 for those that do not, so ascending on that
expression puts real dates first and NULLs last — on MySQL, SQLite and Postgres alike.

A scan for other dialect-specific constructs (`nullsfirst`, `ilike`, `distinct_on`, `array_agg`,
`jsonb`, `RETURNING`, `ON CONFLICT`) found **none** — these two lines are the whole exposure.

---

## Commits

| # | Commit | Touches | Verify |
|---|---|---|---|
| 1 | Order by null-ness instead of NULLS LAST | `analytics.py`, `outcome_service.py` | `GET /analytics/outcomes?user_id=1` returns 200 against **MySQL**, not SQLite — run it against the real database and read the row back |
| 2 | Give the peek button its own grid track | `style.css` | Collapse the library: editor keeps the wide column, rail stays in column 3, nothing wraps. Re-check at 1280 and below, where the button is hidden |
| 3 | Guard the dialect gap | `tests/` | A test that fails today if run on SQLite-only assumptions — see below |

Commit 1 first: it is a live 500 on every dashboard load. Commit 2 is a layout defect the user
can work around by reopening the library. Commit 3 is the one that stops this recurring.

### On commit 3 — what "guard" honestly means here

The real gap is that the suite runs on SQLite while production runs MySQL. Three options, in
increasing cost:

- **A.** A grep-level test asserting no dialect-specific construct appears in `app/` — cheap,
  catches this exact class, and cannot catch semantic differences.
- **B.** Run the existing suite twice, once against a MySQL service — catches everything, needs
  MySQL available wherever tests run.
- **C.** Mark the SQL-heavy tests and run only those against MySQL — middle ground.

**Recommendation: A now, and record B as the real answer.** A costs one small test and would
have caught this exact bug; pretending it is equivalent to B would be the same
confident-but-wrong move the parser fixtures made. The plan says so rather than implying the
gap is closed.

---

## What must not break

- **`.create-launcher { grid-column: 2 / -1 }`** must keep resolving to "editor column onwards"
  in both grids. With the collapsed track added it does.
- **The ≤1280px media query** already hides the peek button and uses two columns; the new track
  must not leak into it.
- **`minmax(0, 1fr)` stays** on the editor track in both grids — a grid child defaults to
  `min-width: auto`, so a long URL in the textarea would otherwise scroll the page sideways
  (`style.css:1444-1446`).
- **Ordering semantics must not change.** Newest published first, undated rows last — the fix
  reproduces exactly that, and the existing outcome tests assert order.
