# Plan — Topic Discovery, Real Image Input, and Style-Faithful Generation

Date: 2026-08-06 (rev 4 — executed)
Branch: `jul-9-contentGeneration-fix-branch`
Status: **IMPLEMENTED — P0 through P6 built and verified**

---

## EXECUTION LOG (2026-08-06)

All phases built. 122 tests passing. What was verified against reality, not just
asserted in tests:

| Claim | How it was checked | Result |
|---|---|---|
| Free discovery finds real posts | Live `ddgs` search, "building in public" | **8 real post URLs**, no key, no cost |
| Public posts are readable unauthenticated | Live fetch of a real post, no cookies | **200, no authwall, JSON-LD layer hit** |
| Full text is available (not just a snippet) | Same fetch | **Complete post body**, author, date, `og:image` |
| The paragraphing fix works | 3 live generations vs a 12-block exemplar | **3/3 exact shape match** |
| Output is not a copy | Similarity gate on those runs | **Jaccard 0.002** vs 0.25 limit |
| Uploads reach LinkedIn's expected path shape | Real server, real 768×768 PNG upload | **Passed** |

### Findings that changed the implementation

1. **The prompt was too prescriptive at first.** Demanding exact per-line word
   counts made the model break sentences mid-clause to hit the number
   (`"The fear of imperfect launch" / "Keeps so many from launching at all."`).
   Shape fidelity was 1/2. Softening word counts to approximate targets and
   requiring clause-boundary breaks took it to **3/3** with natural phrasing.

2. **`metrics_source` was computed two different ways.** The parser keyed it off
   `reactions` alone while the ranking checked all three counts. A live post
   exposed its comment count but not its reaction count and was labelled
   "inferred" despite carrying real data. Now single-sourced.

3. **The AI service silently returns canned marketing copy** when Gemini is
   unreachable (`ai_service.py:54-56, 118-123`). That text is unrelated to the
   exemplar, so the similarity gate passed it happily — the user would have
   received "Excited to share my latest insights on..." believing it was written
   for them. Now detected and raised.

4. **The test suite could not run.** `get_engine()` is `@lru_cache`d, so its
   aiomysql pool bound to whichever event loop touched it first; pytest-asyncio
   gives each test a fresh loop, so tests inherited dead connections and hung.
   Fixed with a per-test SQLite engine and a dependency override. This was
   pre-existing and blocking, so it went into P0.

### Post-release fix — discovery results never appeared (2026-08-07)

**Symptom:** clicking *Find posts* showed backend activity but the Discovered
posts list stayed empty forever.

**Cause — a deviation from this plan.** §3.6 rail 5 says *"Discovery is a
background job, never inline in a request."* It was implemented inline anyway,
with a comment arguing that was simpler. Two consequences compounded:

1. The fetcher paces at ≥30s per post by design, so a 10-post run holds the
   request open for 5+ minutes.
2. `get_session` commits only when the endpoint *returns*, so every row stayed
   inside one uncommitted transaction for that entire window. The UI's
   `GET /discovery/posts` was correctly reporting an empty table.

Confirmed in the data: the abandoned `marketting` run left **no job row and no
posts at all** — the transaction rolled back and took everything with it.

**Fix:**
- `POST /discovery/search` now creates the job, commits it, and returns `202`
  with the job id (**measured: 0.3s**, was minutes).
- The run executes on its own session via `start_job()`, committing after each
  post so results become queryable as they land.
- Failures are recorded on the job row, so a crashed run cannot leave the UI
  polling a job that will never finish.
- The frontend polls the job and refreshes the list each tick.
- `generate_from_topic` passes `stop_after_usable=1` — that path needs one
  exemplar, so it no longer pays ~30s of pacing for candidates it will discard.

**Verified live:** posts appeared incrementally 1 → 2 → 3 while the job ran,
with the visible count matching the job's `fetched_count` at every poll.

### Deferred

- **P4.5 Jina spike not run.** `direct` egress works, so the spike is not
  blocking. The `jina` strategy is built, wired and tested — flip
  `DISCOVERY_EGRESS=jina` to use it. Procedure in §16.
- **SearXNG provider** is implemented but unexercised; `ddg` is the default and
  is working.

---

---

## 0. What you asked for

1. Add your **own image** to a post — from local disk or from a web URL — alongside AI generation.
2. Give the bot a **topic** → it finds the **top-performing LinkedIn posts** on that topic and shows them on your dashboard (post data, or at minimum a clickable link).
3. The bot **remixes** those posts — similar-sounding content and hashtags, reordered, never copied.
4. **Kill the "text only" post path** — every post has an image now.
5. Fix the **banal paragraphing** — output should clone the exemplar's line/paragraph rhythm exactly.
6. Do it **without putting your LinkedIn account at risk.**

### Your answers from round 1 (now baked in)

| Question | Your answer | Effect on this plan |
|---|---|---|
| Budget | **Free only** | Paid providers (Apify/Bright Data) demoted to a deferred, opt-in adapter. Free automated pipeline is now the primary design — §3 rewritten. |
| Image mandatory? | **Always offered, never required** | Confirmed §6. |
| Keep `combined` blend? | **Keep it** | Kept as an option; single exemplar becomes the default. |
| Browser extension (Tier 3) | **Rejected — "I don't want to do anything manually"** | Cut entirely. Manual paste demoted from *default* to *escape hatch*. Full automation is now the design centre. |
| Retention | **Fingerprint after expiry + let me delete after review** | §7/§10 — review-and-delete flow, bulk delete, auto-purge job. |
| Similarity threshold | **"We'll test it out"** | Made runtime-configurable + a tuning harness so you can dial it against real output. |
| Fetch egress | **(c) Build both, default direct, flip via env** | Pluggable egress interface built in P4 — §3.5.1. No decision is load-bearing; switching is one env var. |

---

## 1. Where the code stands today

| Concern | Location | State |
|---|---|---|
| Reference posts | `app/references/sub1..sub2/ref-*.txt` (20 files) | Manual filesystem curation only |
| Disk → DB sync | `app/services/reference_loader.py:94` | Runs once at startup (`app/main.py:41-52`) |
| Style extraction | `app/services/style_service.py:68` | Returns **aggregate averages only** |
| Prompt assembly | `app/services/content_generation_service.py:16,100` | Fills averages into a static template |
| Text/image gen | `app/services/ai_service.py` | Gemini text w/ model fallback; fal.ai image w/ Pollinations fallback |
| Image prompt derivation | `app/services/image_prompt_service.py:37` | 7-element prompt from post text |
| Publishing | `app/services/post_service.py:76-91` | Branches on `post.image_url` presence |
| LinkedIn upload | `app/services/linkedin_service.py:74` | 3-step REST v202503 flow, reads a local file path |
| Post model | `app/database/models.py:42` | `content`, `image_url`, `status`, `scheduled_time` |
| Frontend | `index.html`, `js/app.js` (871 lines), `js/api.js` | 3 tabs: Dashboard / Create / History |
| Post-type selector | `index.html:167-181`, `app.js:162-180` | The thing to delete |
| Migrations | `app/database/migrations/versions/` | Alembic wired via `alembic.ini:8` |

**There is currently no networking to LinkedIn for reading anything.** The only LinkedIn calls are OAuth + publish. Clean starting point.

---

## 2. Root cause of the "banal AI paragraphing"

A real defect with a specific cause, not a prompt-tuning problem.

`extract_style_profile()` (`style_service.py:68-107`) reduces every reference post to scalars: `avg_word_count`, `avg_line_count`, `avg_hashtag_count`, plus one categorical `line_rhythm` from a single boolean threshold (`avg_words / avg_lines < 12`).

Those scalars are the *only* structural signal reaching the model (`content_generation_service.py:100-111`). **The reference text itself is never in the prompt.** Then line 119 says:

```python
prompt += "\nNote: The paragraphing structure MUST match the styling of the reference posts..."
```

The model is told to match reference posts it has never been shown. It falls back on its default LinkedIn-post prior — the bland uniform-paragraph voice you're seeing.

Averaging compounds it: averaging 20 posts across 2 creators erases the exact feature you want (a deliberate one-line paragraph). The mean of a bimodal distribution is a value that occurs in neither mode.

### The fix — three tracks

**Track A — Layout skeleton (deterministic extraction).** New `app/services/layout_service.py`. From **one exemplar post**, extract a per-block fingerprint instead of an average:

```python
@dataclass
class LineSpec:
    words: int
    chars: int
    ends_with: str          # "." | "?" | ":" | "…" | "" | "→"
    has_emoji: bool
    is_hashtag_line: bool
    is_list_item: bool      # leading -, •, 1., ✅

@dataclass
class BlockSpec:
    lines: list[LineSpec]
    blank_after: int        # 1 or 2 — preserves double-gap rhythm

@dataclass
class LayoutSkeleton:
    blocks: list[BlockSpec]
    total_blocks: int
    hook_lines: int
    hashtag_placement: str  # "trailing_block" | "inline" | "none"
    hashtag_count: int
    emoji_positions: list[int]
```

**Track B — Render the skeleton as an explicit line-by-line template**, not prose instructions:

```
Block 1: 1 line, 6 words, ends with "."
(blank)
Block 2: 1 line, 4 words, no terminal punctuation
(blank)
Block 3: 3 lines — 9 / 7 / 11 words, each ending "."
(blank)
Block 4: hashtags only, 5 tags, one line
```

Also pass the exemplar verbatim, fenced and labelled **STRUCTURE REFERENCE — do not reuse any wording, names, numbers, or claims.** That's what actually transmits rhythm; it raises copying risk, which Track C handles.

**Track C — Post-process enforcement + similarity gate.**
- `enforce_layout(text, skeleton)` — deterministic reflow. Blank-line pattern and block count corrected in code, not left to the model. Models drift on formatting; regex doesn't.
- `similarity_gate(generated, source)` — word-trigram Jaccard + longest common word-run. Regenerate (max 2 retries) above threshold, then hard-fail rather than silently ship a near-copy.

**Thresholds are config, not constants** (per your "we'll test this out"):

```python
SIMILARITY_JACCARD_MAX = 0.25      # env: SIMILARITY_JACCARD_MAX
SIMILARITY_MAX_WORD_RUN = 8        # env: SIMILARITY_MAX_WORD_RUN
SIMILARITY_MAX_RETRIES = 2
```

Every generated variation carries its score into the API response and onto the UI card, plus a tuning harness (`scripts/tune_similarity.py`) that runs N generations against a fixed exemplar set and prints the score distribution — so you set the number from data, not vibes.

**Default changes** to a single exemplar. `combined` stays as an explicit option (you asked to keep it), but blending is what causes genericness, so it's no longer what you get by default.

---

## 3. Fully-automated discovery on a $0 budget

You want zero manual steps: topic in, top posts + images out. That's achievable free, but the ground shifted under this since my first draft, and two of the options I listed no longer exist.

### 3.1 What changed in the search-API market (verified 2026-08-06)

| Provider | Status | Consequence |
|---|---|---|
| **Bing Web Search API** | **Retired 11 Aug 2025.** Replaced by "Grounding with Bing Search" inside Azure AI Agents — Azure-only, 40–483% more expensive. | Dead as a free option. |
| **Google Custom Search JSON API** | 100 free queries/day, **but closed to new customers since 2025**; existing users only, until **1 Jan 2027**. | Unusable unless you already hold a key. Even then it sunsets in ~17 months. |
| **Brave Search API** | Free tier **killed Feb 2026**. New users get $5/mo credit (~1,000 queries) then metered billing at $5/1k. Pre-existing free subscribers grandfathered. | No longer free for a new signup. |
| **Proxycurl** | **Dead.** LinkedIn sued Jan 2025 (fake accounts, mass scraping); shut down permanently **4 Jul 2025**. | Remove from consideration entirely. |

So "$0 → SERP API" from my first draft is **no longer true**. Correcting it rather than planning around a dead option.

### 3.2 What *is* still free and fully automated

Two viable keyless paths:

**A. SearXNG, self-hosted** — free/open-source metasearch, aggregates 70+ engines (Google, Bing, DDG, Brave…), no API key, no per-query cost, JSON output. Runs as one Docker container next to the app. This is the strongest free automated option.
- Caveat: JSON output is **off by default** — must be enabled in `settings.yml`.
- Caveat: the underlying engines rate-limit *by IP*. At our volumes (a few dozen queries/day) that's a non-issue; at thousands/hour it degrades to empty results.

**B. DuckDuckGo via the `ddgs` Python library** — free, keyless, zero infrastructure. Unofficial (HTML scraping), so it breaks occasionally and rate-limits under load.

**Recommendation: ship both.** `ddgs` as the zero-setup default so it works the moment you clone; SearXNG as the upgrade when you want reliability. Both behind the same adapter, so switching is one env var.

### 3.3 Getting full post text and images — free

SERP gives URLs, authors and snippets. Full text needs the post page itself. The good news: **LinkedIn embeds JSON-LD and Open Graph metadata in public post pages before the authwall applies** — full commentary, author, and `og:image` are readable **without any login**. This is exactly how the commercial no-login scrapers work; the only thing they add is residential proxies to survive volume.

Parser with a fallback chain (each layer degrades gracefully):

```
1. <script type="application/ld+json">   → articleBody, author, datePublished, images
2. og: / twitter: meta tags              → og:title (author), og:description (text), og:image
3. targeted regex on embedded JSON blobs → reaction/comment counts when present
4. SERP snippet only                     → last resort; post still usable as a link
```

Layers 1 and 3 require raw HTML. They are unavailable when the egress strategy returns markdown (§3.5.1) — the parser is told which layers are viable via `FetchResult.content_kind` rather than failing blindly.

**This is the one step that touches LinkedIn.** It is unauthenticated — no cookie, no session, no account. See §3.5 for why that distinction is the whole ballgame, and §3.6 for the throttles.

### 3.4 The end-to-end automated pipeline

```
topic (you type one thing)
  └─> query builder: site:linkedin.com/posts/ "<topic>" (+ synonym/hashtag expansions via Gemini)
        └─> SearXNG | ddgs  →  candidate post URLs (deduped against DB)
              └─> fetch queue (throttled, jittered, cached, circuit-broken)
                    └─> JSON-LD / og: parser  →  text, author, hashtags, og:image, engagement?
                          └─> rank  →  top N to dashboard, with images
                                └─> auto-select exemplar  →  remix  →  draft + original image
                                      └─> review · edit · schedule/publish · delete source
```

Zero manual steps between "topic" and "draft ready for review". The only human action left is the one you actually want: approving what goes out under your name.

### 3.5 Threat model — why this doesn't endanger your account

Account restriction/ban fires on **authenticated automation**:
- Requests carrying your `li_at` session cookie from a non-browser client
- Calls to internal Voyager endpoints (`/voyager/api/...`)
- Machine-pace velocity, zero dwell time, perfectly regular intervals
- Sequential enumeration with no organic navigation graph
- Session IP hopping to a datacenter ASN mid-session
- Ignoring HTTP 999 (LinkedIn's "you're a bot" code) and hammering through it

Unauthenticated access gets **IP-level** enforcement — rate limits, 999, CAPTCHA. Annoying, but **it cannot ban an account, because no account is attached.**

> **Governing rule: your LinkedIn identity is never the identity that reads post data.**
> Your OAuth token (`w_member_social`, publish-only) stays clean as long as it is never used for reads, and no process ever authenticates as you.

**One honest caveat.** In the free design, the unauthenticated fetches originate from your home IP — the same IP your browser uses while logged in. LinkedIn *can* correlate IP→account in aggregate. In practice logged-out traffic doesn't get accounts banned, and the throttles in §3.6 keep volume indistinguishable from a person reading a few posts. But it's a non-zero coupling and you should know it exists.

### 3.5.1 Egress strategy — decided: build both, default direct

Rather than bet the design on an unverified assumption, the fetcher's network egress is a swappable strategy. Roughly 20 lines behind one interface, so the choice is never load-bearing:

```python
# app/services/discovery/egress/base.py
class EgressStrategy(Protocol):
    name: str
    async def fetch(self, url: str) -> FetchResult: ...   # html | markdown, status, final_url
```

```
app/services/discovery/egress/
    base.py       EgressStrategy Protocol + FetchResult
    direct.py     httpx straight to linkedin.com — your IP          [DEFAULT]
    jina.py       GET https://r.jina.ai/<url> — Jina's IP, keyless
    proxy.py      httpx via HTTP(S)_PROXY — your VPN/proxy of choice
    registry.py   env-driven selection + automatic fallback chain
```

Config:

```python
DISCOVERY_EGRESS = "direct"            # direct | jina | proxy
DISCOVERY_EGRESS_FALLBACK = "direct"   # used when primary fails or trips its circuit
DISCOVERY_PROXY_URL = ""               # only read when egress=proxy
```

**Ships defaulting to `direct`** — zero setup, works on clone, and the throttles keep volume tiny. Flipping to `jina` after the spike (§16) is a one-line `.env` change with no code touched.

Design points worth stating up front:

- **Per-strategy circuit breakers, not one global.** If Jina starts returning blocked/empty responses, that must trip Jina's breaker and fail over to `direct` — not halt discovery entirely. Conversely a 999 from `direct` must not be misread as a Jina failure.
- **The parser must accept both shapes.** `direct` returns raw HTML (JSON-LD + og: tags intact); `r.jina.ai` returns rendered **markdown**, which strips `<script>` blocks — so **the JSON-LD layer of the §3.3 fallback chain is unavailable under Jina**. `FetchResult.content_kind` (`html` | `markdown`) tells the parser which layers are viable. This is the main reason `direct` stays the default: it yields strictly richer data.
- **Throttles are global, not per-strategy.** The daily cap and inter-request delay apply across all egress paths, so switching strategies can never be used — accidentally or otherwise — to multiply request volume.
- `proxy.py` exists because it's nearly free once the interface does, and it's the escape hatch if both other paths get blocked.

### 3.6 Non-negotiable safety rails

1. **No LinkedIn session credential anywhere in the codebase.** No `li_at`, no `JSESSIONID`, no config key for one, no "just for testing" branch. CI grep enforces it.
2. **`LinkedInService` gets publish/upload methods only** — never read/search. The OAuth token is structurally incapable of being used for discovery.
3. **Throttle the fetcher hard:** unauthenticated, no cookies, ≥30s jittered interval, hard daily cap (default 40 fetches), realistic browser UA, exponential backoff on 429/999, and a **circuit breaker that trips for 24h after 3 consecutive 999s**. Throttles are enforced **above** the egress layer (§3.5.1) so they apply identically to every strategy.
4. **Cache permanently.** `discovered_posts.post_url` is UNIQUE. A given post is fetched exactly once, ever.
5. **Discovery is a background job**, never inline in a request — gives you status UI and a kill switch.
6. **Parser health check** — if the JSON-LD layer fails on >50% of fetches in a run, flag it in the UI as "LinkedIn markup changed" rather than silently returning empty posts.
7. **Retention** — §7.

### 3.7 Paid providers — deferred, not deleted

You said free. So the paid adapters get built as *interfaces only*, unimplemented, so nothing needs rearchitecting if you change your mind:
- **Apify** (~$30–50/mo) — keyword post-search actors that run without cookies. Best value if free proves too thin.
- **Bright Data** ($500+/mo) — most legally hardened vendor; production-grade.
- **People Data Labs / Coresignal** — wrong shape regardless of budget. Person/company enrichment records, not post content with keyword search. Dropped.

### 3.8 Manual paste — kept, but demoted

You don't want manual work, so it is no longer the default provider or part of the normal flow. It survives only as a one-field escape hatch (`POST /discovery/manual`) for when a post you specifically want isn't discoverable. Costs ~20 lines; saves you when the free path has a bad day. **You will never be required to use it.**

### 3.9 Ranking "top performing" without guaranteed engagement counts

Engagement numbers appear in public markup inconsistently. Rather than pretend otherwise, the score is hybrid and degrades cleanly:

```python
engagement_score = (
      w1 * log1p(reactions)          # when parseable
    + w2 * log1p(comments)           # when parseable
    + w3 * log1p(reposts)            # when parseable
    + w4 * serp_rank_inverse         # always available — search rank as authority proxy
    + w5 * recency_decay             # always available
    + w6 * query_overlap_count       # appeared under multiple related queries
)
```

The UI labels each card's basis honestly — **"measured engagement"** vs **"ranked by relevance"** — so you're never shown a fake metric. Weights live in config.

---

## 4. Remix pipeline (topic → your post)

```
discovered posts (ranked)
  → auto-select top exemplar (override in UI if you want a different one)
      ├─ layout_service.extract_skeleton(exemplar)        → structural clone
      ├─ hashtag_service.remix(exemplar.hashtags)         → near-synonyms, reordered, count preserved
      ├─ content_generation_service.generate(topic, skeleton, remixed_tags, notes)
      ├─ similarity_gate(draft, exemplar)                 → retry ≤2, then hard fail
      └─ enforce_layout(draft, skeleton)                  → deterministic reflow
  → image (§5)
  → review · schedule/publish · delete source post
```

**Hashtag remix** (`app/services/hashtag_service.py`): take the source tag set → Gemini generates 2–3 near-synonym candidates per tag (`#BuildInPublic` → `#ShippingInPublic`, `#BuildingLoud`) → preserve the source's *count*, shuffle order, drop any tag identical to a source tag unless it's an unavoidable generic (`#AI`, `#Python`).

---

## 5. Images — three sources, one of them new

### 5.1 Your own image (local + web URL)

New `app/api/media.py`:

**`POST /media/upload`** — multipart.
- Requires `python-multipart` — **not currently in `requirements.txt`**, must add.
- Content-type allowlist: png / jpeg / webp.
- **Sniff magic bytes; do not trust extension or declared content-type.**
- Max 8 MB, enforced while streaming.
- Re-encode through Pillow → strips EXIF (geolocation!) and defuses polyglot files. Adds a `Pillow` dependency; worth it.
- Normalize webp → PNG, because `linkedin_service.py:112` only distinguishes jpeg from png and would upload a `.webp` mislabelled.

**`POST /media/from-url`** — fetch a remote image. **The one genuinely dangerous new surface** — the app's first user-controlled outbound request:
- Scheme allowlist http/https only.
- Resolve DNS, then reject loopback, link-local (**169.254.169.254** — cloud metadata), `10/8`, `172.16/12`, `192.168/16`, `::1`, `fc00::/7`.
- **Re-run the IP check on every redirect hop** — don't follow blindly.
- 10s timeout, streamed size cap, content-type verification, then the same sniff + Pillow re-encode.

Both converge on a local `/static/uploads/x.png`, so **`post_service.publish_post()` and `linkedin_service.upload_image()` need no changes** — `linkedin_service.py:104-108` already falls back to `uploads_dir / filename`, which is the path that actually resolves.

### 5.2 Discovered post images → original visuals (new capability)

You asked the bot to find "top performing posts, images". The discovered post's `og:image` is captured — and then used as a **visual style reference, never republished**:

```
exemplar og:image
  → Gemini vision: describe composition, palette, typography, framing, mood
    → merge with the 7-element prompt from image_prompt_service.py:11
      → fal.ai nano-banana → an ORIGINAL image in that visual language
```

Same "remix, don't copy" principle as the text — and it sidesteps the copyright exposure of reposting someone's graphic. `ai_service.py` already speaks raw REST to the Gemini API, so adding an `inline_data` image part is a small extension.

The source image is shown on the dashboard card for reference and is deleted with the post (§7).

### 5.3 Model change

Add `image_source` to `posts`: `"generated" | "upload" | "url" | "style_derived" | None`. Provenance only; `image_url` semantics unchanged.

---

## 6. Removing the text-only path

- `index.html:167-181` — delete the "1. Choose Post Layout" section entirely.
- `index.html:338` — remove `hidden` from `#image-generation-section`; renumber the step labels (there are currently two sections both labelled "4").
- `app.js:162-180` — delete the `.type-card` click handler.
- `app.js` — `clearGeneratedImage()` stays (the ✕ remove button still uses it).
- Image UI becomes four tabs in one block: **[AI Generate] [Match discovered style] [Upload] [From URL]**.

**Per your answer: an image is always offered, never required.** The form stays submittable without one, so a fal.ai outage never blocks publishing. "Text only" just disappears as a *choice*.

---

## 7. Retention, review, and deletion

Per your answer — fingerprint survives, raw content is disposable, and you can delete after review.

| Stage | What happens |
|---|---|
| Fetched | Full record: text, hashtags, author, og:image, metrics, `raw_payload` |
| You review it | `reviewed_at` set; card shows a **Delete** button |
| You delete it | Raw text, `raw_payload` and cached image purged immediately. `LayoutSkeleton` + hashtag pool retained (anonymous structure, no source wording) so drafts already built from it stay reproducible |
| 90 days (config `DISCOVERY_RETENTION_DAYS`) | Auto-purge job does the same thing unattended |
| Bulk | "Clear all reviewed" and "Clear all for keyword X" |

Rationale: the structural fingerprint is the only part with lasting value, and it contains none of the source's actual wording — so long-term storage carries no copyright or ToS weight.

---

## 8. Data model & migration

One Alembic revision (`app/database/migrations/versions/`):

```python
class DiscoveredPost(Base):
    __tablename__ = "discovered_posts"
    id, user_id (FK, nullable)
    keyword: str(255), indexed
    source: str(50)              # searxng | ddg | jina | manual | apify | brightdata
    post_url: str(500) UNIQUE    # dedup key
    author_name, author_headline, author_profile_url
    content_text: Text | None    # nulled on purge
    snippet: Text | None
    hashtags: JSON
    image_url: str(500) | None   # cached og:image; nulled on purge
    posted_at: DateTime | None
    reactions, comments, reposts: int | None   # None = unavailable, NOT zero
    metrics_source: str(20)      # "measured" | "inferred"
    serp_rank: int | None
    engagement_score: Float
    layout_skeleton: JSON | None # survives purge
    fetched_at, reviewed_at, purged_at, expires_at: DateTime | None
    raw_payload: JSON | None     # nulled on purge
    used_as_reference: bool

class DiscoveryJob(Base):
    __tablename__ = "discovery_jobs"
    id, user_id, keyword, provider
    status: str(20)              # queued|running|success|partial|failed|circuit_open
    requested_count, found_count, fetched_count, parse_failures: int
    error: Text | None
    created_at, completed_at
```

Plus `posts.image_source` (nullable String(20)).

`reference_posts` is untouched. Content generation reads exemplars through one accessor resolving from *either* table, so a discovered post and a curated `ref-N.txt` are interchangeable as style sources.

Note `reactions`/`comments`/`reposts` are **nullable, not defaulted to 0** — "we couldn't read it" and "it got zero" must not be the same value, or the ranking silently lies.

---

## 9. API surface

```
POST   /media/upload                    multipart      → {image_url, image_source}
POST   /media/from-url                  {url}          → {image_url, image_source}

POST   /discovery/search                {keyword, limit, provider?}  → {job_id}
GET    /discovery/jobs/{job_id}                        → status, counts, circuit state
GET    /discovery/posts?keyword=&sort=engagement       → list[DiscoveredPost]
POST   /discovery/manual                {url?, text?}  → DiscoveredPost      (escape hatch)
DELETE /discovery/posts/{id}                           → purge raw, keep skeleton
POST   /discovery/purge                 {keyword?, reviewed_only?}  → bulk
POST   /discovery/posts/{id}/reviewed                  → mark reviewed

POST   /generate/remix                  {topic, exemplar_id, exemplar_kind, notes, num_variations}
                                                       → {variations[], similarity_scores[]}
POST   /generate/from-topic             {topic}        → full auto: discover → rank → remix → image
GET    /reference/layout/{kind}/{id}                   → LayoutSkeleton (UI preview)
```

`/generate/from-topic` is the headline endpoint — one call, topic in, reviewable draft + image out. `POST /generate/styled-post` stays for backward compatibility.

---

## 10. Frontend

New **Discover** tab (4th sidebar item):
- Topic input + **"Find & draft"** (runs the full auto pipeline) and **"Just find posts"** (discovery only).
- Live job progress: searching → fetching (n/m) → parsing → ranking. Circuit-breaker state surfaced honestly if it trips.
- Result cards: author · og:image thumbnail · snippet · **engagement badge labelled "measured" or "relevance-ranked"** · date · **Open on LinkedIn ↗** · [Use as style] · [Delete].
- Bulk actions: "Clear reviewed", "Clear keyword".
- Active provider badge (`ddgs` / SearXNG / manual).

**Create** tab changes:
- Post-layout selector removed.
- "Style source" control: `Discovered post ▾ | Reference file ▾ | Combined blend`.
- **Layout skeleton preview** — shows the block/line shape being cloned, so you can see *before* generating that it'll produce that half-line paragraph.
- Similarity score badge per variation (green / amber / rejected) + a threshold slider wired to config, so you can tune live.
- Image block: AI Generate / Match discovered style / Upload / From URL.

---

## 11. Phasing

| # | Phase | Depends on | Notes |
|---|---|---|---|
| **P0** | Foundations — `python-multipart` + `Pillow` + `ddgs` deps, config keys, Alembic revision, discovery adapter skeleton | — | No behavior change |
| **P1** | **Style fidelity rebuild** — `layout_service.py`, skeleton-driven prompt, `enforce_layout`, similarity gate + tuning harness | P0 | **Biggest visible win. Needs no discovery at all** — works on your existing 20 reference files today |
| **P2** | Media input — upload + from-url, SSRF/magic-byte/Pillow hardening, UI tabs | P0 | |
| **P3** | Remove text-only path | P2 | Small, mechanical |
| **P4** | Discovery core — tables, background job runner, `ddgs` provider, throttled fetcher, **egress layer (`direct` + `jina` + `proxy`, defaulting to `direct`)**, JSON-LD/og parser, Discover tab | P0 | First LinkedIn contact — unauthenticated only |
| **P4.5** | **Jina egress spike** — 30 min, verify `r.jina.ai` isn't blocked by LinkedIn and what its markdown yields vs raw HTML | P4 | Cheap, non-blocking; outcome decides whether `DISCOVERY_EGRESS` flips |
| **P5** | Ranking + retention — hybrid score, review/delete, auto-purge job | P4 | |
| **P6** | Full auto — `/generate/from-topic`, hashtag remix, og:image → visual-style-transfer images | P1, P4, P5 | The "one topic in, draft out" experience |
| **P7** | *(optional)* SearXNG provider + paid adapter stubs | P4 | Reliability upgrade when free path frays |

**P1 first** is deliberate: it fixes your loudest complaint, needs no external dependency, and carries zero account risk.

---

## 12. Tests

- `test_layout_service.py` — a one-word paragraph must survive as a one-line block.
- `test_layout_enforcer.py` — idempotence; block/blank-line count matches skeleton exactly.
- `test_similarity_gate.py` — verbatim copy rejected; genuine paraphrase passes; 8-word shared run trips hard fail.
- `test_media_upload.py` — `.png`-named JPEG caught by magic-byte sniff; >8MB rejected; EXIF stripped.
- `test_media_ssrf.py` — `169.254.169.254`, `127.0.0.1`, `10.x`, and a public→private **redirect** all rejected.
- `test_discovery_parser.py` — JSON-LD fixture, og-only fixture, and authwall fixture each degrade to the right layer.
- `test_discovery_throttle.py` — daily cap enforced; 3× 999 opens the circuit; open circuit makes zero requests; **cap holds across an egress switch** (can't be reset by changing strategy).
- `test_discovery_egress.py` — registry honours `DISCOVERY_EGRESS`; unknown value falls back to `direct`, never to nothing; a failing primary fails over to `DISCOVERY_EGRESS_FALLBACK`; **breakers are per-strategy** (tripping Jina leaves `direct` usable); `content_kind` correctly gates the parser's JSON-LD layer.
- `test_retention.py` — purge nulls text/raw/image but preserves `layout_skeleton`.
- `test_ranking.py` — `None` metrics never rank as zero; `metrics_source` labelled correctly.
- CI grep: no `li_at` / `JSESSIONID` anywhere in the repo.

---

## 13. Housekeeping (unrelated to features)

1. **Your fal.ai key is in plaintext in `notes/add layers.md`, and `notes/` is not in `.gitignore`** — a `git add .` would commit it. Rotate the key; ignore the file.
2. `check_fal_models.py` / `check_gemini_models.py` / `scratch/` / `error.log` (28 KB at repo root) are dev leftovers — clean up during P0.

---

## 14. Open risks, stated plainly

| Risk | Likelihood | Mitigation |
|---|---|---|
| Free SERP (`ddgs`) rate-limits or breaks | **High** over time | SearXNG fallback (P7); adapter makes the swap one env var |
| LinkedIn changes public markup, parser breaks | Medium | 4-layer fallback chain; parser health check surfaces it instead of silently returning nothing |
| Home IP gets 999'd | Medium | Throttles + circuit breaker; flip `DISCOVERY_EGRESS=jina` or `proxy` — built in P4, no code change |
| `r.jina.ai` blocked by LinkedIn | **Unknown — spike in P4.5** | Non-blocking: `direct` is the default and Jina is only an alternative path. Worst case the strategy stays unused |
| Jina's markdown loses JSON-LD → thinner data | Likely | `content_kind` gates the parser; `direct` stays default precisely because it yields richer data |
| Engagement counts often unavailable | High | Hybrid ranking + honest UI labelling; never fabricate a metric |
| Free path proves too thin overall | Medium | Apify adapter stub is interface-ready; ~$30–50/mo unblocks it without rearchitecting |

---

## 15. What I will NOT build

- No automation authenticated as your LinkedIn account.
- No `li_at` / session-cookie handling, in any form, for any reason.
- No headless browser driving linkedin.com.
- No fake or burner accounts.
- No verbatim republication of anyone's post text, hashtag set, or image.
- No reuse of the OAuth publishing token for reading.

---

## 16. Decisions — all settled

**Egress: (c) — build both now, default to `direct`, flip via env once Jina is verified.** Specified in §3.5.1, built in P4, verified in P4.5.

Nothing else is open. The plan is ready to execute.

### P4.5 spike protocol (~30 min, run once P4's egress layer exists)

Small, bounded, and the result is a config value — not a rewrite.

1. Pick 5 already-discovered public post URLs spanning different authors and ages.
2. Fetch each twice — once via `direct`, once via `r.jina.ai` — spaced by the normal 30s throttle.
3. Record per URL: HTTP status, whether an authwall was returned, presence of JSON-LD, presence of `og:description`, extracted text length, `og:image` present, engagement counts present.
4. **Decision rule:**
   - Jina succeeds on ≥4/5 **and** still yields usable post text → keep both, leave `direct` as default (richer data), with Jina as the documented fallback for when the home IP gets 999'd.
   - Jina succeeds but text is materially thinner (og-only, truncated) → keep it strictly as a **degraded fallback**, never primary.
   - Jina blocked or empty → leave the strategy in place, unused; `proxy` becomes the real decoupling option.
5. Write the outcome into this file as a dated note. No code changes either way — only `.env`.

Worth stating: because `direct` ships as the default and the fallback chain already handles failure, **P4.5 never blocks anything.** If the spike never happens, the system still works exactly as designed.
