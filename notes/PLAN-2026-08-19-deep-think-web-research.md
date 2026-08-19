# Plan — Deep Think: web research before generating

Date: 2026-08-19
Branch: `jul-9-contentGeneration-fix-branch`
Status: **PROPOSED — not started**

A toggle beside Generate Draft. When it is on, the topic box is read as a research brief: the
app searches the web for what is actually being said about those keywords, condenses the
findings, and puts them in front of the model before it writes. The topic box also becomes a
textarea, because a brief is not a single line.

---

## 1. What already exists (and what does not)

| Need | Exists? | Where |
|---|---|---|
| Web search | **Yes, but LinkedIn-scoped** | `ddgs==9.14.4`; `providers.py:134` |
| General-web queries | **No** | `build_queries` hardcodes `site:linkedin.com/posts` (`providers.py:121-123`) |
| Page fetching, paced + circuit-broken | Yes | `DirectEgress`, `RateLimitedFetcher` |
| Readable-text extraction from arbitrary pages | **No** | `parse_post` is LinkedIn-specific |
| Condensing text with a model | Yes | `AIService.generate_with_gemini` |
| Placeholder-response detection | Yes | `is_template_fallback` (`ai_service.py:32`) |
| A staged progress banner | Yes | `setDraftStage` (`app.js:584`), stages `writing` / `image` |

**No new dependency.** `ddgs`, `beautifulsoup4` and `lxml` are all installed already.

The LinkedIn scoping is deliberate — discovery looks for *exemplar posts*. Deep Think looks for
*facts about a topic*. Same library, different query, so this is a separate service rather than
a flag on the discovery pipeline.

---

## 2. Design decisions, with the reasoning

**Its own fetch budget, not discovery's.** Sharing `discovery_daily_fetch_cap` would let a busy
research day starve exemplar discovery, and the two have different value per request. Separate
cap, same circuit breaker — being blocked belongs to the IP, not the subsystem.

**Bounded hard, because the user is waiting.** Top 5 results, fetched in parallel, each page
truncated before it reaches the model. Research that cannot finish inside its budget returns
what it has rather than blocking the draft.

**The summariser is guarded like the classifier.** `AIService` returns canned marketing copy
when Gemini is unreachable (`ai_service.py:54-56, 118-123`). Injected into a research brief
that becomes prompt context, that copy would silently steer the whole post. `is_template_fallback`
refuses it — the same non-negotiable as taxonomy guard 6.

**Silence is reported, never papered over.** If search returns nothing, or every page fails, or
the summariser refuses, the draft is still generated *without* research and the result carries a
note saying so. The existing precedent is the imageless draft in `remix_service.py:166-168`.
A post that reads as researched but was not is the failure mode to avoid.

**Off by default.** It costs a search, up to five fetches and a model call per generate.

**Third-party pages, so: public pages only, browser UA, existing pacing, no credentials.** The
same properties `egress/strategies.py:6-10` already documents for discovery.

---

## 3. The pipeline

```
topic textarea ──► search (ddgs, no site: filter, top 5)
                        ▼
              fetch pages in parallel (own budget, shared circuit)
                        ▼
              extract readable text (bs4), truncate per page
                        ▼
              condense to research notes (Gemini, fallback-guarded)
                        ▼
              build_prompt(..., research=notes) ──► generate
```

Research notes reach the prompt the same way the post type does — as a labelled block appended
in `build_prompt`, not woven into the instructions, so it is inspectable and easy to remove.

---

## 4. Commits

| # | Commit | Touches | Verify |
|---|---|---|---|
| 1 | Topic becomes a textarea | `index.html`, `style.css` | Same id, so `serialize()`/`hydrate()` and the pinned field-set test pass **untouched** — that is the guard |
| 2 | `research_service`: search, fetch, extract, condense | new service + tests | Fake search + fake pages: returns notes; a Gemini outage returns *no* notes rather than canned copy; zero results is a clean empty, not an error |
| 3 | Thread research into the prompt | `content_generation_service`, `remix_service`, `generate.py` | The notes appear in the prompt; without them the prompt is byte-identical to today |
| 4 | The Deep Think toggle | `index.html`, `app.js`, `style.css` | `deepThink` joins `serialize()`; the pinned field-set assertion **must be updated** — expected, and the reason that test exists |
| 5 | A `researching` stage in the handoff | `app.js` | Banner reads "Researching the topic…" before "Writing the draft…" |

Commits 1 and 2 are independent and can land first. Commit 3 is inert until 4 turns it on.

---

## 5. What must not break

- **No silent prompt change.** With Deep Think off, `build_prompt` must produce exactly what it
  produces today. Commit 3 ships with a test asserting that.
- **`ai-text-prompt` keeps its id.** `serialize()` reads it by id and `.value` works on both
  elements, so the field set is unchanged by commit 1 — only commit 4 adds a field.
- **The similarity gate still runs.** Research changes what the model knows, not whether the
  draft is checked against its exemplar.
- **Discovery's budget is untouched.** A research run must never consume exemplar-fetch quota.
- **Never present unresearched as researched.** Every failure path degrades to a plain draft
  *with a note*, matching how a failed image already degrades.

---

## 6. Open risk worth naming

Research quality is unmeasurable here. There is no ground truth for "did the web notes make the
post better", and the honest position is that this feeds the model more context and does not
promise more accuracy. If the notes are wrong, the post will be confidently wrong — so the
condensing prompt should ask for claims *with their source*, and the notes should be shown to
the user rather than hidden, letting them judge before publishing.

That last point is a design constraint, not a nice-to-have: an invisible research step that
silently shapes a post the user then publishes under their own name is the wrong trade.
