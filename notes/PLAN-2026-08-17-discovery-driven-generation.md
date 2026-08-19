# Plan — Parallel Discovery, Discovery-Driven Generation & a Self-Healing Feedback Loop

Date: 2026-08-17 (rev 7 — S0, S1, P0–P3, P5, P6 executed)
Branch: `jul-9-contentGeneration-fix-branch`
Status: **IN PROGRESS — S0 ✅ · S1 ✅ · P0–P6 ✅ · P7 outstanding**

## EXECUTION LOG — S1 (2026-08-18)

**Rendered tier: NO-GO.** Measured against 35 real post URLs, plain HTTP and headless Chrome 151.

| | plain HTTP | headless Chrome |
|---|---|---|
| reactions readable | **32/35 (91%)** | 8/8 on the same subset |
| counts only rendering could read | — | **0/8** |
| rescued the plain tier's 3 misses | — | **0/3** |
| median wall-clock | **1.10 s** | 7.96 s |
| memory | negligible | ~800 MB browser tree |
| blocked / authwalled | 0/35 | 0/11 |

§2.5's premise was wrong. LinkedIn does **not** hydrate the post's own counts client-side — they are server-rendered as `data-num-reactions` / `data-num-comments` on the `social-actions__*` anchors. The parser reads 0/35 reactions because it greps for `"numLikes"` / `"reactionCount"`, JSON keys that appear **nowhere** on these pages. Nothing needs rendering; the fast tier needs a working extractor.

Two further findings, both live defects:

1. **JSON-LD `commentCount` is a false zero on 6 of 32 posts** — it counts the inlined `comment[]` array, not real comments. One post shows 1,113 reactions and 26 comments while the JSON-LD reports `commentCount: 0` and `LikeAction: 0`. `describe_basis()` then labels the row `"measured"`, so the UI presents a confident wrong zero — the precise failure §5a's invariant forbids.
2. **Reposts are not published at all** (0/35, both tiers). `RANK_W_REPOSTS` weights a term that is `None` on every row.

The parallel-rendering-block question is moot: a tier with 0% information gain does not get built at any block rate, so no further live requests were spent on it.

Harness: `scripts/spike_rendered_counts.py` (re-runnable). Full write-up: `~/.anvideck/projects/linkedin-bot/ref/GROUND_TRUTH_LINKEDIN_PUBLIC_POST_COUNTS.md`.

**Consequence for P4/P7:** ⑨/⑩ become real via a parser fix, not a browser.

## EXECUTION LOG — P4 (2026-08-19)

Shipped as eight commits rather than one, each independently verifiable.

| Commit | What |
|---|---|
| `c7c63d9` | `retarget_skeleton()` — reshape a skeleton to N blocks |
| `f4e79c0` | ② paragraph count reaches the exemplar path |
| `298c537` | `post_types` table, six seeds |
| `df505e7` | Classifier + all six anti-explosion guards |
| `b3db44b` | ③ classification during discovery, parallel then serial |
| `f1ca21c` | Merge proposals — detection, API, Post types view |
| `e2995bf` | ① discovery picker in Create Post |
| `6bc4d82` | ④⑤ Generate Draft clones the chosen post, steered by type |

**Both plan checks pass.** Cloning a 5-block exemplar with the count set to 3 yields exactly 3 blocks with the similarity gate still passing; feeding the classifier a Gemini outage refuses rather than creating a type.

**The paragraph control had to be structural.** Asking for "3 paragraphs" in prose is the same class of instruction `layout_service` exists to replace — and worse here, because `enforce_layout` would still hold the *exemplar's* block count and reshape the output straight back to it. The control would have looked like it worked and done nothing. Retargeting the skeleton first makes the template, the prompt and the enforcement agree on one number.

**Classification is a second parallel stage, not a step in the write loop.** Eager classification means a model call per post; run inside the serial section they would queue up and turn a 30-post search from seconds into minutes. The wave now fetches concurrently, parses, proposes types concurrently, then writes serially — the taxonomy crossing into workers as plain dicts for the same reason the fetcher never sees `db`. Enabling it also revealed that the discovery tests were reaching the live Gemini API: the suite went from 12s to 82s and real model latency landed inside the fetcher's timing assertions, which failed. An autouse fixture disables it, and the tests that exercise it inject a fake.

**Three defects the work surfaced:**

1. `runStagedHandoff` opens with `startNewPost()`, which clears the exemplar — so reading `this.exemplarId` lazily inside the thunk sent `null` and the request was rejected. The Discover-tab path never hit it because it closes over the post it was clicked on. Now captured before the handoff starts.
2. The plural rule in slug normalisation stripped `-es` unconditionally, turning `listicles` into `listicl` — a slug that would then never match the type it meant. It now only strips after a sibilant.
3. An earlier draft called a `markDirtyNow()` that does not exist. Nothing needs it: `exemplarId` is in `serialize()`, so choosing one moves `serialize() !== lastSaved` by itself.

**Guard 2 finds paraphrase and narrowing, not synonymy.** Nothing relates `narrative` to `story` without a stemmer or embeddings. That is why the growth brake and the merge pass exist rather than being optional, and the code says so instead of implying more.

## EXECUTION LOG — count extractor fix (2026-08-18)

⑨/⑩ are now real. `parser.py` reads the counts off the post's own social-actions anchors and no longer consults the JSON-LD figures. All 35 stored rows re-read via `scripts/backfill_post_counts.py` (dry by default).

| | before | after |
|---|---|---|
| `reactions` populated | 0/35 | **32/35** |
| `comments` populated | 35/35, six of them false zeros | **21/35, all verified** |
| `reposts` populated | 0/35 | 0/35 — never published |
| `metrics_source` | 35 `measured` | 32 `measured`, 3 `inferred` |

The like-range filter went from excluding 100% of results to filtering 32 of 35.

**The trap that nearly shipped:** `data-num-reactions` is not unique — it occurs up to **11 times** per page on byte-identical anchors, because the related-posts rail repeats the whole card. First-match works today only because the post's card precedes the rail. The extractor selects structurally instead: the `article.main-feed-activity-card` with no `section.related-posts` ancestor. First-match had already cost accuracy — it credited five posts with neighbours' comment counts (26/35 readable vs the true 21/35).

**Why the old tests never caught any of this:** they asserted against `'"numLikes":842'`, a JSON key that appears nowhere on a real page. The fixtures were invented rather than captured, so the suite stayed green while the feature was 0% effective in production. Fixtures now model captured markup, rail included, and two tests pin the rail exclusion — one with the rail after the post, one with it before, so the rule cannot decay into "take the first card".

## EXECUTION LOG — P2 (2026-08-17)

**156 tests pass** (141 before, +15). Verified in headless Chrome as well as by test.

| Ask | Delivered |
|---|---|
| ⑯ 25–30 per search | Cap raised 25 → 30; the client now asks for 30, not 10 |
| ⑰ Pagination, ≥7/page | Client-side, 7/page. A search is one small response, so paging slices rather than re-requests |
| ⑱ Hashtag search bar | Own input. `normalise_hashtags` accepts `#ai`, `ai`, comma- or space-separated, and dedupes case-insensitively |
| ⑧ Recency reach | `timelimit` (d/w/m/y) on the search, defaulting to **past month**. Verified against `ddgs.DDGS._search_sync`'s real signature, not assumed |
| ⑮ Loading animation | Sweeping skeleton cards while a search runs |
| ⑭ Image, text, author URL | Thumbnail + linked author name + headline on every card |
| ⑬ Button | `btn-sm`, left-aligned, "Find & draft" |

**Two things worth recording.**

*`author_profile_url` was stored but never serialised.* The column has always been populated by the parser; `DiscoveredPostResponse` simply never carried it, so a card could not link to the author despite the app holding the URL. Found by checking rather than by symptom.

*The hashtag query design.* Tags are searched as **their own angle**, not appended to every query. A post tagged `#BuildInPublic` often does not repeat the topic words in its body, so folding the tag in would have narrowed results while looking like it widened them. Tags are also capped at 3 — every extra tag is two more live search requests.

*Schema:* migration `b7e2d41c8a55` adds `hashtags` and `timelimit` to `discovery_jobs`. Necessary because `POST /discovery/search` only queues; a background task re-reads the row, so anything not persisted is silently dropped between queueing and running — the search would have quietly ignored what you asked for.

*Breakage handled:* widening the `DiscoveryProvider` protocol broke every test double at once (15 failures). Doubles now take `**_options`, so the next option added does not repeat that.

**Verified visually:** 7 cards on page 1, pager reading `1–7 of 16`, thumbnails and author links present, and measured-vs-inferred still displayed honestly (`412 reactions · 37 comments` versus `ranked by relevance`).
Source: `prompt.md` (2026-08-17) + your revisions

---

## EXECUTION LOG (2026-08-17)

**130 tests pass** (123 before, +7 new concurrency tests). Nothing committed.

### S0 — own-post analytics spike ✅ **My hypothesis was wrong, in your favour**

Impressions for personal member posts **are** available. I had guessed they were
organisation-only; the docs say otherwise.

| | |
|---|---|
| Endpoint | `GET /rest/memberCreatorPostAnalytics` (`q=entity` per post, `q=me` aggregated) |
| Permission | **`r_member_postAnalytics`** — not restricted |
| Metrics | IMPRESSION, MEMBERS_REACHED, REACTION, COMMENT, RESHARE, POST_SAVE, LINK_CLICKS, FOLLOWER_GAINED_FROM_CONTENT, … |
| Aggregation | `TOTAL` or `DAILY` + `dateRange` — a real time series, which is what `post_metrics` wanted |

So **㉒ is buildable, and ㉓'s "my stats" half is unblocked.** We do *not* need
`socialMetadata`, whose `r_member_social_feed` is documented **Restricted —
"granted to select developers only."** Design against the analytics API instead.

Written up with endpoint, URN encoding and version caveats at
`~/.anvideck/projects/linkedin-bot/ref/GROUND_TRUTH_LINKEDIN_MEMBER_ANALYTICS.md`.

**⚠ Incidental finding, unrelated to this work but live:** `linkedin_service.py:21`
pins `LINKEDIN_VERSION = "202503"`. The docs state Marketing 202507 has been
**sunset**, and supported monikers run 2025-08 → 2026-07. **The existing publish
path is on an unsupported API version.** Not caused by anything here; worth
fixing before it stops working.

### P0 — reference subsystem deleted ✅

26 files removed (22 reference texts, loader, API, schemas, its test). Two tables
dropped via migration `a1c4f7b93d20`. `generate_styled_post`, `_load_posts` and
`POST /generate/styled-post` gone; frontend picker, style panel and 4 API methods
gone.

Two deliberate deviations from the plan:

- **`select_representative` kept.** The plan said delete it. It is a pure
  function over `list[str]` with its own tests and no reference coupling — the
  blend-picking logic is reusable when P4 lets you select several exemplars.
- **`test_generation.py` rewritten, not deleted.** Its three tests went through
  the deleted endpoint, but two assertions — that `hook_style` and `word_type`
  actually reach the prompt — were covered *nowhere else*. Moved down to
  `build_prompt`, where they belonged.

Also removed as newly-dishonest: the **Word Count** select (`generate_with_layout`
has ignored `num_words` since the skeleton rewrite) and the **Variations** select
(only the deleted styled path consumed it). A control that does nothing is worse
than no control.

The app stays working between P0 and P4: Generate Draft falls back to the plain
`/generate/text` path, which never depended on references.

### P1 — parallel fetch engine ✅

Ported from the profiled prototype. Producer/consumer in `run_discovery`: fetch
workers touch the network and never `db`; one writer owns the session. Wave
dispatch bounds early-stop overshoot. Savepoint per row absorbs duplicate URLs.

**A second real bug, caught by the tests rather than review:**

```
5.67s for 12 fetches at 0.10s latency — serial would be 1.20s
```

Five times *slower* than serial. `TokenBucket` captured `rate` in `__init__`, and
the fetcher is a module-level singleton — so the rate was pinned at import and
`discovery_requests_per_second` was **a dead knob**. That directly contradicts
this plan's own safety story ("the first move is lowering the rate, not touching
code"). Rate, burst and concurrency now read from settings on every use.

New tests, `tests/test_fetcher_concurrency.py` — each covers a case that passes a
smoke test and fails under load:

| Test | Guards |
|---|---|
| `test_parallel_run_persists_every_post` | A1 — the session bug this restructure exists for |
| `test_daily_cap_holds_under_concurrency` | cap of 5, 20 candidates → exactly 5 requests |
| `test_block_halts_the_wave` | block at 3 of 30 → ≤12 sent, job marked `partial` |
| `test_stop_after_usable_overshoot_is_bounded` | A3 — overshoot ≤ worker count, not the wave |
| `test_duplicate_url_does_not_abort_the_run` | A2 — 9 sent, 6 unique stored |
| `test_rate_ceiling_is_respected` | the burst bug |
| `test_parallel_is_actually_faster_than_serial` | stops a future change quietly re-serialising |

Retention changed 90 → 30 (defect A4's backfill migration still belongs to P5).

### Follow-ups for you

1. **`.env.example` is stale** — it documents `DISCOVERY_MIN_INTERVAL_SECONDS=30`
   and `DISCOVERY_DAILY_FETCH_CAP=40`. The file is behind a permission guard I
   cannot write to. New keys: `DISCOVERY_REQUESTS_PER_SECOND`,
   `DISCOVERY_CONCURRENCY`, `DISCOVERY_CONCURRENCY_MAX`, `DISCOVERY_ADAPTIVE`,
   `DISCOVERY_RAMP_AFTER`, `DISCOVERY_TOKEN_BURST`.
2. **The pinned LinkedIn API version** (S0's incidental finding).
3. **Nothing is committed** — the working tree holds P0 + P1 and the earlier
   create-post work.

---

## 0. What changed in this revision

| Your call | Effect |
|---|---|
| Don't assume 6 workers — **verify** the limit, expect 2–3 | §2.1 rewritten around **measured** results. A prototype was built and profiled; it found a real bug. Answer: **3 workers**, and the reason is not caution — extra workers do nothing |
| Calendar: modernise the UI, month + time + year | §2.8. Read-only month grid with year/month navigation and a proper time picker. No drag-to-reschedule |
| Gemini classifies post type; **new types self-register** without asking | New §2.7. Dynamic taxonomy with anti-explosion guards |
| Other open questions: go with the suggestions | §8 resolved |

---

## 1. The requirements

**Create Post** — ① remove the reference-text section, show fetched Discovery posts · ② select paragraph count · ③ save the reference post with a post type · ④ type options available when using the post · ⑤ Generate Draft uses the selected post · ⑥ track every post used, shown in Discovery history · ⑦ keep 30 days then remove

**Discovery** — ⑧ reach more recent posts · ⑨ read reaction counts · ⑩ filters: like range, last week / month / year / 2–3 years · ⑪ history with multi-select delete · ⑫ confirm before delete, listing posts · ⑬ "Find & draft" smaller, left-aligned · ⑭ cards show image, text, author URL · ⑮ real loading animation · ⑯ 25–30 posts per search · ⑰ pagination, ≥7 per page · ⑱ hashtag search bar

**Dashboard** — ⑲ links to posts published from here · ⑳ which discovery post each was drafted from · ㉑ modernised calendar

**Analytics & learning** — ㉒ track impressions / likes / comments, refreshed 24h + on restart · ㉓ self-healing: compare my post's performance against its exemplar's and feed it back

---

## 2. Design decisions

### 2.1 Parallel fetching — measured, not assumed

You asked me not to guess at the worker count. I built the engine as a prototype and profiled it against a local server that mimics LinkedIn's observable behaviour (latency spread, 999 / 429 / 403 responses). Harness: `scratchpad/worker_probe.py`.

**It found a real bug before any of this reached the codebase.**

First run, with the breaker checked only at task entry:

```
2 workers -> server hit 30 times (ok=5 blocked=25 halted=0)
6 workers -> server hit 30 times (ok=5 blocked=25 halted=0)
```

The server started refusing at request 5, and **all 30 requests were still sent.** `asyncio.gather()` admits every worker at once, so all 30 cleared the "is the circuit open?" check before the first response came back. The breaker was decorative.

The fix is to re-test the breaker *after* queueing and immediately *before* the request, and refund the reserved budget when a worker bails. After that:

```
2 workers -> server hit  9 times (ok=5 blocked=4 halted=21)
3 workers -> server hit 10 times (ok=5 blocked=5 halted=20)
6 workers -> server hit 13 times (ok=5 blocked=8 halted=17)
```

This is the first hard argument for a low worker count: **when the site says stop, 2 workers send 9 more requests and 6 workers send 13.** Concurrency is measured in how much damage a bad run does.

#### How many workers is actually right

The decisive measurement. In-flight requests are governed by Little's law — `rate × latency` — so above that, workers idle. 30 posts, varying both:

| req/s cap | latency | needed in-flight | **w=2** | **w=3** | w=6 | w=12 |
|---|---|---|---|---|---|---|
| 2.0 | 0.8s | 1.6 | 15.3s | 15.3s | 15.3s | 15.3s |
| 3.0 | 0.8s | 2.4 | 12.5s | **10.5s** | 10.5s | 10.5s |
| 3.0 | 2.0s | 6.0 | 30.5s | 20.8s | 11.8s | 11.7s |
| 5.0 | 2.0s | 10.0 | 30.4s | 20.5s | 11.1s | 7.9s |

**The finding: requests/second is the real knob; worker count is derived from it.** At 3 req/s and sub-second latency, 3 workers is the knee — 6 and 12 are exactly as fast, they just sit idle. Going to 6 buys nothing and, per the breaker test, sends 44% more requests after a block.

So 2–3 workers isn't a cautious compromise. At the rates we want, **it is the optimum**, and the number 6 from rev 2 was wrong.

#### Two more things the profiling caught

**The token bucket was over-delivering.** With burst capacity equal to the rate, the bucket starts full and fires `rate` requests simultaneously on the first tick — a thundering herd, which is the worst possible opening move against a bot detector. Measured at an 8 req/s cap: **10.72 req/s observed**. With burst clamped to 1: **7.93 req/s**. The cap now actually holds.

**Budget accounting is atomic.** 30 URLs, 8 workers, cap of 10 → server received exactly 10. Reserving before dispatch under the lock is what makes that hold; checking after the fetch would have overrun it.

#### Proposed settings

| Setting | Value | Why |
|---|---|---|
| `discovery_requests_per_second` | **2.0** | The safety knob. Start below what we think is tolerated |
| `discovery_concurrency` | **3** | = ceil(rate × expected latency). Measured knee |
| `discovery_concurrency_max` | 6 | Ceiling for the adaptive ramp |
| `discovery_token_burst` | **1** | No opening herd |
| `discovery_daily_fetch_cap` | 40 → **300** | Backstop, not a pacer |
| `discovery_min_interval_seconds` | 30 → **0** | Superseded by the bucket |

**Expected: 30 posts in ~15s at 2 req/s, versus ~17.5 minutes today.**

#### The honest limit of this verification

This proves the **engine** is correct — that the breaker halts a wave, the cap can't be overrun, and the rate ceiling holds. It does **not** tell us what LinkedIn tolerates. That number is unknown, varies by IP and history, and the only way to measure it directly is to trip it — which is the thing we're trying to avoid.

So the engine discovers it instead of me guessing: **adaptive concurrency**, verified working —

```
clean run:   limit ramped 2 -> 8 over 40 fetches, no blocks
blocked run: limit reached 4, collapsed to 2 on the first block,
             16 requests sent of 40, 24 halted
```

Start at 2 workers / 2 req/s, ramp one step after every 5 consecutive successes, collapse to the floor on the first block signal (`is_blocked` already covers 429 / 999 / 403 — `egress/base.py:39-45`), and open the breaker after 3. If blocks appear in practice, the first move is lowering `discovery_requests_per_second`, not touching code.

#### Fetch in parallel, write serially — this is not optional

**`AsyncSession` is "not safe for use in concurrent tasks"** — verbatim from `sqlalchemy/ext/asyncio/session.py:210-211`, enforced by `IllegalStateChangeError` in `orm/state_changes.py`.

`run_discovery()` currently interleaves the two: `await fetcher.fetch()` → `parse_post()` → `db.add()` → `await _save()`, all inside one per-candidate loop (`service.py:142-167`), sharing one session. Making that loop concurrent shares the session across tasks and breaks it.

```
   candidates ──► N fetch workers ──► asyncio.Queue ──► 1 writer (owns the session)
                  (no DB access)                        db.add + commit, serial
```

Fetching parallelises; persistence does not. The queue also preserves `commit_each`'s incremental visibility, which is what lets the UI show posts as they land.

### 2.2 Paragraph count only — writing style untouched

`enforce_layout(text, skeleton)` already coerces output to a target block count, taking it from `len(skeleton.blocks)`. **One new function** — `retarget_skeleton(skeleton, n_blocks)` — stretches or collapses a discovered post's skeleton to your chosen paragraph count, keeping line rhythm proportional.

```
discovered post → extract_skeleton() → retarget_skeleton(n=5) → enforce_layout()
                       ↑                                              ↑
                 unchanged                    (new)              unchanged
```

The prompt builder, `generate_with_layout()`, `extract_style_profile()` and the similarity gate are **not touched**. No per-paragraph word counts reach the prompt — which also avoids the failure in `notes/PLAN-discovery-media-style.md`, where hard per-line counts broke sentences mid-clause (1/2 shape fidelity) and soft targets scored 3/3.

### 2.3 30-day retention with surviving history

`discovery_retention_days` 90 → 30; the purge job already runs (`scheduler_service.py:51-56`).

Today `purge_post()` keeps the row and strips content so drafts stay reproducible (`service.py:301-313`). Hard-deleting as you asked would orphan the history in ⑥/⑳ and destroy the exemplar numbers ㉓ depends on. **Fix: denormalise** — copy the exemplar's URL, author, snippet and metrics onto the lineage row at generation time, then the `discovered_posts` row can be hard-deleted at day 30 exactly as asked.

### 2.4 Deleting the reference subsystem

Traced every import. **`style_service.py` stays** — a pure function over `list[str]`, called on the *exemplar text* by `remix_service.py:137` and `content_generation_service.py:226`. That is the writing style you're keeping.

| Delete | Keep |
|---|---|
| `app/references/sub1/`, `sub2/` (22 files) | `style_service.py` |
| `services/reference_loader.py` | `layout_service.py` |
| `api/reference.py`, `schemas/reference.py` | `generate_with_layout()` + everything it calls |
| `ReferenceProfile` / `ReferencePost` models + tables | `similarity_service.py`, `remix_service.py` |
| `main.py:41-52` startup sync | |
| `content_generation_service`: `_load_posts()` (:105), `select_representative()` (:77), `generate_styled_post()` (:288) | |
| `POST /generate/styled-post` (`generate.py:79`) | |
| `tests/test_reference_loader.py` | |
| Frontend: profile select, ref checkboxes, style panel, `initCreatorProfiles`, `loadCreatorStyleProfile`, `calculateStyleFromPosts`, `handleSelectedPostsChange`, `updateStyleProfileUI`, 4 `API.*` methods | |

**Accepted consequence:** discovered posts become the *only* exemplar source. No readable discovery result means drafting fails with an error rather than falling back — correct behaviour, but it makes Discovery load-bearing.

### 2.5 Reading reaction counts

`parser.py` walks JSON-LD → OG → embedded-JSON regex over raw HTML, but LinkedIn hydrates counts client-side, so a plain fetch often has no numbers. `ranking.py:72-76` then honestly reports `metrics_source: "inferred"`.

| Option | Real counts? | Cost | Verdict |
|---|---|---|---|
| Current HTTP + JSON-LD/regex | Sometimes | Free, parallelises perfectly | Keep as the fast tier |
| **Headless browser** (Chrome already installed) | **Likely** — runs the JS that fills the counts | 150–300 MB/instance, 2–5s/page | **Spike it (S1)** |
| `r.jina.ai` | **No** — strips `<script>`, extracts *less* | Free | Rules itself out |
| Apify / Bright Data | Yes | Money — you rejected this (`providers.py:20-22`) | Only if that changes |
| LinkedIn official API | No — not for third-party posts | — | Dead end |

**Two tiers:** fast parallel HTTP across the whole result set, rendered pass only for posts you filter by or clone. The expensive path stays bounded by your clicks.

### 2.6 Own-post analytics — still gated on S0

- Scope is `w_member_social openid profile email` (`oauth_service.py:29`) — write-only.
- `LinkedInService` has **no analytics read methods** at all.
- The catalogue flags missing LinkedIn REST ground truth as the top gap, so I won't assert what LinkedIn exposes.

Hypothesis for S0 to confirm or kill: likes/comments on your own share are more reachable than impressions, which are typically an organisation analytic. **⑲ needs none of this** — `linkedin_post_id` is already stored (`models.py:52`).

### 2.7 Self-extending post-type taxonomy

Gemini classifies each discovered post. If nothing fits, it coins a new type and that type **registers itself without asking you**, then future posts can be classified into it.

```
post text + current taxonomy ──► Gemini ──► existing slug
                                       └──► {slug, label, description, why_new}
                                                    │
                                          guards (below) ──► post_types row
                                                             origin = "ai"
```

Seed list, per your acceptance of §8: `story`, `contrarian`, `listicle`, `case_study`, `announcement`, `question`.

**The failure mode this must not have is taxonomy explosion.** Unconstrained, a model coins `storytelling`, `personal_story` and `narrative` as three separate types within a week, and the classification becomes worthless. Auto-add is your decision and I'm building it; these guards are about keeping it *useful*, not about asking permission:

1. **Slug normalisation** — lowercase, underscores, singularised. `Personal Story` and `personal_story` are the same row.
2. **Near-duplicate snap** — compare the proposal's label + description against every existing type; above a similarity threshold, snap to the existing type instead of creating one. Reuses `similarity_service`, which already does trigram overlap.
3. **Justification required** — the model must state why none of the existing N fit. No reason, no new type.
4. **Growth brake** — past ~20 active types the novelty bar rises and a merge pass is proposed.
5. **Usage decay** — a type unused for 90 days is flagged for merge, so one-off coinages don't accumulate.
6. **Fallback detection — non-negotiable.** `ai_service.py:54-56` and `:118-123` silently return canned marketing copy when Gemini is unavailable. With auto-add and no human in the loop, that text becomes a permanent post type named after *"Excited to share my latest insights"*. The classifier reuses `is_template_fallback()` and refuses to classify rather than storing garbage.

Every type carries `origin` (`seed` | `ai`), `first_seen_at` and `usage_count`, so you can see what the model invented and when — auto-add without permission, but never without a record.

### 2.8 Calendar (㉑)

There is no calendar today — only `<input type="datetime-local">` (`index.html:465`). This is a build, not a restyle.

Scope, per your note: a **month grid** showing scheduled posts on their dates, with **month and year navigation** and a proper **time picker**, styled to match the existing dark glassmorphic theme. Read-only — clicking a day filters/scrolls to that day's posts. **No drag-to-reschedule** (materially more work; say the word if you want it later).

---

## 3. The self-healing loop (㉓)

```
   generate ──► draft_lineage row
                 ├─ exemplar snapshot (url, author, reactions/comments when used)
                 └─ params used (type, paragraph count, hook, tone, image y/n)
   publish ──────────────────┐
                             ▼  +24h, +72h, +7d
                       post_metrics (my post — time series, not a snapshot)
                             ▼
                  outcome = my_engagement ÷ my_rolling_median
                             ▼
              aggregate by params ──► bias exemplar ranking + defaults
```

**Compare against yourself, not the exemplar.** A creator with 100k followers gets 400 reactions on a mediocre post; you might get 12 on an excellent one. That ratio measures follower count, not writing — and follower counts aren't reliably available to normalise it away. So the two numbers do different jobs:

- **Exemplar stats** answer *"was this worth cloning?"* → used at **selection** time to rank candidates.
- **Your stats** answer *"did this choice work for me?"* → measured against **your own rolling median**, used after the fact to steer parameters.

Collapsing them into one ratio produces a confident number that means nothing — the same trap `ranking.py` already avoids by never treating an unread count as zero.

**Two stages, because attribution needs data.** Across type × paragraph count × hook × tone × image, anything under ~20–30 published posts is noise.

- **Observation** — record everything, show the comparison, change nothing. Honest from post #1.
- **Feedback** — bias only once a combination has enough samples, and always display the sample count. A recommendation backed by 3 posts must say "3 posts".

`post_metrics` is a **time series** on purpose: a single current number can't distinguish a post that died in 6 hours from one still climbing at day 7, and that difference is most of the signal.

Gated on both spikes. If S0 fails but likes/comments are reachable, the loop still runs on those. If neither is, ㉓ isn't buildable and I'll say so rather than ship a panel of zeros.

---

## 4. Data model changes

```
discovered_posts   + post_type_id FK, post_type_source (ai|manual)
                   + hashtag_query, metrics_tier (http|rendered), metrics_checked_at

post_types         NEW — slug, label, description, origin (seed|ai),
                         first_seen_at, usage_count, active          (§2.7)

draft_lineage      NEW — post_id, discovered_post_id, denormalised exemplar
                         snapshot, params_used JSON, outcome_ratio   (§2.3, §3)

post_metrics       NEW — time series; gated on S0                    (§3)

DROP               reference_profiles, reference_posts               (§2.4)
```

Retention 90 → 30. Fetcher settings per §2.1.

---

## 5. Phases

**S0 and S1 run first, in parallel.** Research only, no product code.

### S0 — Own-post analytics spike
Can impressions be read for a personal member post, with which scope/endpoint? Likes/comments? Does your app tier grant it or need approval? What does re-consent cost? → Ground Truth doc + go/no-go on ㉒ and half of ㉓.

### S1 — Reaction-count spike ✅ *(2026-08-18 — NO-GO on the rendered tier)*
Headless Chrome against 5–10 real public post URLs. How often do rendered pages expose counts a plain fetch misses? Per-page cost? Does parallel rendering trip blocks faster than parallel HTTP? → hit-rate number + go/no-go on the rendered tier. Makes ⑨/⑩ real rather than decorative.

**Answer: never.** 0/8 gain on readable pages, 0/3 rescue on unreadable ones, 7× the wall-clock, ~800 MB. The counts were in the plain HTML all along, under `data-num-reactions`. See the S1 execution log at the top.

### P0 — Delete the reference subsystem
Everything in §2.4's left column, plus a migration dropping both tables.
**Verify:** app boots, no dead imports, remaining suite passes, `remix_from_post` still generates.

### P1 — Parallel fetch engine
Port the profiled prototype into `RateLimitedFetcher`: semaphore + token bucket (burst 1), lock reduced to bookkeeping, budget reserved before dispatch, **breaker re-checked after queueing**, adaptive ramp/collapse.
**Verify — these are the tests that matter, ported from the harness:**
- 30 URLs under 20s at 2 req/s
- cap of 10 with 8 workers → exactly 10 requests reach the server
- block at request 5 → wave halts in single digits, not 30
- measured req/s ≤ configured cap (the burst bug)
- adaptive limit collapses to floor on first block
- **a full parallel run persists every post with no session error** (A1 — the writer is the only coroutine touching the session)
- **`stop_after_usable=1` overshoots by at most `concurrency`**, not by the whole wave (A3)
- **a wave containing duplicate URLs stores the unique set and absorbs the collisions** (A2)
- **no `db.add` appears anywhere in the discovery fetch path except the writer** — a grep-level guard against the next contributor reintroducing A1

### P2 — Discovery UI
⑯ 25–30 results, ⑰ pagination at 7/page, ⑱ hashtag bar, ⑮ real loading state, ⑭ image + text + author URL, ⑬ button small and left-aligned, ⑧ recency reach.

### P3 — Filters, history, multi-select delete
⑩ date-range and like-range filters with an explicit "N of M have measured counts" disclosure — unread counts are excluded and reported, never treated as zero. ⑪ history sub-tab. ⑫ multi-select delete via the existing `<confirm-modal>`, listing posts and flagging any used for a real draft.

### P4 — Create Post on discovery exemplars + taxonomy ✅ *(2026-08-19)*
① discovery picker replaces the deleted reference UI · ② paragraph count via `retarget_skeleton` · ③④ post-type classification with the self-extending taxonomy and all six guards from §2.7 · ⑤ Generate Draft through the existing `remix_from_post()`.
**Verify:** clone a 5-block exemplar with count set to 3 → exactly 3 blocks, similarity gate still passes. Feed the classifier a Gemini outage → refuses, does not create a type.

### P5 — Lineage + 30-day retention
⑥ lineage row per generation with denormalised snapshot, history of used posts · ⑦ hard-delete at 30 days — **ships after the denormalisation, never before**. Migration backfills `expires_at` on existing rows (defect 2) and the lineage FK uses `ondelete="SET NULL"` (defect 4).
**Verify:** a row past its new 30-day expiry is actually deleted; a lineage row pointing at it survives with its snapshot intact.

### P6 — Dashboard
⑲ permalinks (no new scopes) · ⑳ "drafted from" provenance · ㉑ month/year/time calendar per §2.8 · ㉒ analytics section if S0 passed.

### P7 — Self-healing loop *(gated S0 + S1)*
Observation stage, then feedback stage with sample thresholds shown.

---

## 5a. The ten defects — solved or made to fail safely

Auditing rev 3 against the code turned up ten problems in the plan. They split cleanly in two, and the split is the point: **five are structural and get designed out; five involve genuinely absent data and get a fail-safe instead**, because you cannot compute your way to a number nobody published.

### Group A — solved by design (verified)

Harness: `scratchpad/db_probe.py`, run against SQLAlchemy async + aiosqlite.

**A1. Parallel fetch on a shared session.** `run_discovery` writes to the DB inside the fetch loop (`service.py:142-167`), and `AsyncSession` is "not safe for use in concurrent tasks" (`sqlalchemy/ext/asyncio/session.py:210-211`). Reproduced immediately:

```
A. naive shared AsyncSession across 12 concurrent tasks
   -> InvalidRequestError: Session is already flushing        CONFIRMED BROKEN

B. fetch in parallel, write serially (3 fetchers -> queue -> 1 writer)
   -> stored 12/12, no session error                          PASS
```

**Fix — architectural, not defensive.** Fetching parallelises; persistence does not. One writer owns the session for the run's lifetime; no other coroutine touches it.

```
candidates ──► N fetch workers ──► asyncio.Queue ──► 1 writer (sole session owner)
               (zero DB access)                       add + commit, serial
```

*Fail-safe on top:* the writer is the only place `db.add` appears in the discovery path, so a future contributor adding a write inside a fetch worker breaks a test rather than production.

**A2. Duplicate URLs collide on the unique constraint.** Dedup is checked once before the loop (`service.py:121`) but `post_url` is `unique=True` (`models.py:125`), so two concurrent jobs — or one wave containing the same post twice — hit an `IntegrityError`. Verified:

```
C. duplicate URLs in one wave (12 sent, 8 unique)
   -> stored 8 unique of 12 sent, 4 collisions absorbed       PASS
```

**Fix:** per-row `IntegrityError` → rollback that row only, treat as "already known", continue. The alternative — one duplicate aborting a 30-post run — is the failure mode worth designing out.

**A3. `stop_after_usable` defeated by parallelism.** The auto-draft path passes `stop_after_usable=1` (`remix_service.py:197`) *because* serial fetching is expensive. Dispatching the whole wave spends the budget before the first result lands. **Fix:** dispatch in waves of `concurrency`, re-check the stop condition between waves. Verified:

```
D. conc=1: fetched 1 to get 1 usable, overshoot 0
   conc=3: fetched 3 to get 1 usable, overshoot 2
   conc=6: fetched 6 to get 1 usable, overshoot 5
   (dispatching all 12 at once would overshoot by 11)         PASS
```

Overshoot is bounded by `concurrency` — 2 wasted fetches at the recommended setting, not 29. *Fail-safe:* the overshoot is budget-accounted, so it can never breach the daily cap.

**A4. Retention change is not retroactive.** `expires_at` is computed at insert from the setting (`service.py:297`), so 90 → 30 only affects new rows — every post already stored keeps a 90-day expiry and your "delete after 30 days" silently doesn't happen for months. **Fix:** the migration backfills `expires_at = fetched_at + 30 days` on existing rows. *Fail-safe:* the purge job logs how many rows it expired each run, so "0 purged, forever" is visible instead of silent.

**A5. Hard-delete hits a foreign key.** `draft_lineage.discovered_post_id` pointing at a row deleted on day 30 either blocks the delete or orphans the lineage. **Fix:** `ondelete="SET NULL"` plus the denormalised snapshot (§2.3). *Fail-safe:* history renders from the snapshot columns, so a null FK degrades to "source no longer stored" rather than a blank row or a crash.

### Group B — one principle, applied five times

The remaining five are the same failure wearing different clothes: **presenting absent data as if it were known.** This codebase already has the rule and states it plainly at `ranking.py:10-13` —

> a missing count is `None`, never `0`. Treating "could not read the number" as "the number is zero" would push every unreadable post to the bottom regardless of how it actually performed — a confident-looking ordering built on absent data.

Extending that rule to the new surfaces is the fail-safe. Each case: **exclude, disclose, never substitute.**

| | Where the unknown appears | Fail-safe |
|---|---|---|
| **B1** | **Like-range filter** over posts whose counts never parsed | Excluded from the range, and the list states "showing N of M with measured counts" |
| **B2** | **Date filter** — `posted_at` is often unreadable; `recency_factor` already concedes it by returning 0.5 for `None` (`ranking.py:29-31`) | Same treatment. I had disclosed this for likes and missed it for dates; it is the identical hole |
| **B3** | **`engagement_score` staleness** — computed at insert, but the rendered-metrics tier fills counts in later and nothing recomputes (`service.py:287-294`) | Recompute on every metrics write, exactly as `_bump_overlap` already does. Plus `metrics_checked_at` surfaced, so a score computed from nothing is labelled, not trusted |
| **B4** | **Permalinks (⑲).** I called these free. `linkedin_post_id` is whatever the `x-restli-id` header returned (`linkedin_service.py:70`); no test asserts its shape and nothing builds a URL from it | Validate the stored value against a known URN shape. **No match → render the post with no link**, rather than a link that 404s. S0 confirms the real format |
| **B5** | **Rolling median baseline** — I gated parameter attribution on sample size but not the baseline it divides by. A median of 2 posts is noise | Withhold the outcome ratio entirely below the floor and show "not enough history yet" — never a number with no meaning behind it |

And one that is neither, but is a fail-safe all the same:

**B6. "Refresh on restart" can flood the API.** ㉒ asks for every 24h *or* on restart; a crash loop turns that into a request storm. **Fix:** refresh on restart only if the last capture is older than a threshold, with a per-day ceiling mirroring the discovery budget.

### What this changes about the plan

Group A moves work into P1 and P5 and adds three tests that would otherwise have been written after the first production failure. Group B adds no new mechanism at all — it applies a rule the codebase already follows to five places the plan had quietly forgotten it.

*(Harness note: `db_probe.py` mirrors the shape of `discovered_posts` and the write loop on SQLite rather than importing the app's models, so it proves the pattern, not the app's exact code. A teardown traceback after probe A is harness noise — disposing the engine while probe A's deliberately-broken session still holds a connection.)*

---

## 6. Risks

1. **Concurrency bugs are silent** — demonstrated twice, not theorised. The prototype sent all 30 requests through a tripped breaker and looked fine doing it; auditing the plan then turned up three more consequences of concurrency the serial design had made impossible (§5a defects 1, 3, 8). Assume there are further ones and keep P1's test list adversarial.
2. **The engine is verified; LinkedIn's tolerance is not** (§2.1). Mitigated by adaptive ramp from a low floor and settings-not-code tuning.
3. **Deleting the reference subsystem makes Discovery load-bearing** (§2.4). Accepted deliberately.
4. **Auto-registering types can explode or absorb garbage** (§2.7). Six guards, of which fallback detection is the one that must not be skipped.
5. **The feedback loop can produce confident nonsense at low N** (§3). Observation before feedback; always show the sample count.
6. **Two spikes gate three deliverables.** If both fail, ⑨/⑩ degrade to today's behaviour and ㉒/㉓ aren't buildable. Better known in a day than after the UI is built.

---

## 7. Scope boundary

**In:** `services/discovery/*`, `api/discovery.py`, `api/generate.py`, `remix_service.py`, `content_generation_service.py` (removals + `retarget_skeleton`), `layout_service.py`, `scheduler_service.py`, `linkedin_service.py` (read methods if S0 passes), `models.py` + migrations, frontend.

**Out:** the publish path, similarity thresholds, the prompt builder and writing engine (§2.2 — explicitly unchanged), OAuth beyond whatever S0 concludes.

---

## 8. Open questions — resolved

| | Question | Resolution |
|---|---|---|
| 1 | Sentiment auto or manual? | **Auto** via Gemini, overridable |
| 2 | Which type options? | Seed list of 6, **self-extending** per §2.7 |
| 3 | Calendar scope | **Modernised month grid + year nav + time picker**, read-only (§2.8) |
| 4 | ⑲ scope | **Posts published through this app only** — needs no new scopes |
| 5 | Starting concurrency | **3 workers / 2 req/s**, adaptive to 6 — measured, §2.1 |

---

## 9. Ready to implement

Nothing is blocking. Proposed order: **S0 + S1 first** (a day, no product code, decides whether two phases exist), then **P0 → P1 → P2 → P3 → P4 → P5 → P6 → P7**.

The prototype from §2.1 is working and profiled, so P1 is a port rather than a design exercise.
