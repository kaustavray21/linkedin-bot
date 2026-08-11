# linkedin-scout — Architecture & Build Log

What this is, how it works, and the full decision trail of how Claude built it —
including the two approaches that were tried and abandoned before landing on the
current design.

## 1. What it does

```
uv run linkedin_scout.py --topic "computer vision" --days 7 --limit 50
        │
        ▼
  ranked spreadsheet of the topic's most recent/engaging LinkedIn posts,
  with real reaction/comment/repost counts
```

One script, one external dependency (Apify), no LinkedIn login, no browser.

## 2. Data flow

```
 CLI args (--topic --days --limit)
        │
        ▼
 payload = {keywords:[topic], maxItems:limit, sortBy:"date_posted", datePosted:bucket}
        │
        ▼
 SHA256(payload) ──► output/cache/apify_<hash>.json exists? ──yes──► load from disk (no charge)
        │no
        ▼
 POST https://api.apify.com/v2/actors/data_pool~linkedin-post-scraper/run-sync-get-dataset-items
   ?token=$APIFY_API_TOKEN                                    (Apify's managed LinkedIn
        │                                                       account pool does the actual
        ▼                                                       LinkedIn access — not us)
 JSON array of post dicts  ──► cached to output/cache/apify_<hash>.json
        │
        ▼
 to_post(item) per item ──► Post dataclass
   .score = reactions + 2*comments + 3*reposts
   .data_quality = full | partial | none   (based on how many of reactions/comments/reposts are non-null)
        │
        ▼
 sort: [scored posts by .score desc]  +  [unscored posts by .published desc]
        │
        ▼
 write_xlsx() or write_csv()  ──►  output/<topic-slug>.xlsx
```

## 3. Why it's shaped this way — the decision trail

### 3.1 The starting ask
"Build a scraping pipeline: take a topic, find the most recent and most engaging
posts, list them — LinkedIn only."

Two things needed pinning down immediately: **how** to reach LinkedIn data (LinkedIn's
User Agreement prohibits scraping and they pursue both technical countermeasures and
legal action against scrapers), and **what "engaging" means** numerically.

### 3.2 First access-method decision: public search indexing
Given a choice between (a) logged-in browser automation, (b) a paid third-party
scraping API, (c) unauthenticated public-search discovery, or (d) LinkedIn's official
API, the user picked **(c) public search indexing** — no LinkedIn login at all, lowest
risk, since it never touches linkedin.com directly for search.

Engagement metric was fixed at this point too: **composite score = likes + comments×w +
reposts×w**, weighted higher for comments/reposts since they signal deeper engagement
than a passive like.

### 3.3 Build v1 (abandoned): DuckDuckGo HTML search + anonymous page scraping
Built a script that:
1. Queried `html.duckduckgo.com/html/` with `site:linkedin.com/posts "<topic>"`, using
   `df=` params to bucket the `--days` recency window.
2. Parsed result links out of the DDG results page (unwrapping DDG's
   `//duckduckgo.com/l/?uddg=` redirect wrapper).
3. Fetched each candidate LinkedIn post URL anonymously and tried to pull engagement
   counts out of embedded JSON-LD (`interactionStatistic`) or regex fallback patterns
   in the raw HTML, since anonymous/logged-out post pages often don't render real
   counts at all.
4. Cached every fetch to disk to avoid re-hitting the same URL.

**Tested live and it failed at step 1**: the very first request to DuckDuckGo's HTML
endpoint came back with a bot-detection challenge page (`anomaly.js?cc=botnet`), not
search results. DDG's HTML endpoint actively fingerprints and blocks unauthenticated
scripted requests — it wasn't a selector bug, it was a hard wall.

**Decision made here**: don't try to defeat that detection (spoof the challenge,
rotate headers/proxies to evade fingerprinting, etc.). That crosses from "using a
public search surface" into "circumventing anti-bot protection," which wasn't a road
worth going down for a personal tool. The search backend needed to change to
something that wants to be called programmatically — i.e. an actual API.

### 3.4 The "estimates aren't good enough" pivot
Before rebuilding the search backend, the deeper problem surfaced: even with a
working search backend, **anonymous LinkedIn post pages mostly wall off real
reaction/comment/repost counts behind a JS-hydrated login prompt** — v1's
JSON-LD/regex extraction would have been best-effort at best, frequently returning
`data_quality: none`. The user was explicit: *"I want promising output not an
estimation... find a way we can get correct post with correct numbers."*

That reframed the problem. Getting **real** engagement numbers for arbitrary public
LinkedIn posts, for an individual, realistically has exactly three roads:
1. LinkedIn's official API — a dead end for this use case; it's Partner-Program-gated
   for things like managing your own company page, not searching other users' posts.
2. A paid third-party scraping service that operates its own LinkedIn access on its
   own infrastructure, so your personal account is never involved.
3. Browser automation logged into your own LinkedIn account — free, but it's bulk
   automated activity on your real account, which LinkedIn detects and can restrict
   or ban for.

Presented as an explicit tradeoff (cost vs. your-account-risk vs. accuracy), the user
chose **option 2**.

### 3.5 Researching the actual third-party option
Rather than guessing at an API shape, this was verified against live sources:
- `WebSearch` surfaced several Apify LinkedIn actors; `data_pool/linkedin-post-scraper`
  ("LinkedIn Posts by Keywords, No Cookies") matched the need — keyword search,
  managed account pool, no user login required.
- The actor's **build metadata was pulled directly from Apify's public API**
  (`GET /v2/acts/data_pool~linkedin-post-scraper/builds/<id>`) rather than trusting the
  marketing page — this caught a real discrepancy: a summarized web page had said
  `datePosted` accepts `"24_hours"`; the actual input schema says `"24h"`. Building
  against the wrong literal would have silently broken every recency-filtered call.
- Confirmed pricing directly from the API: pay-per-event, **$0.0015/post
  ($1.50/1,000)**, no subscription.
- Confirmed the call pattern (`POST .../run-sync-get-dataset-items?token=...`, 300s
  sync timeout) against Apify's own docs.

### 3.6 Build v2 (current): direct Apify actor call
v1's entire DDG-search + anonymous-page-scrape stack was deleted, not patched — the
old approach genuinely couldn't produce real numbers, so keeping it around as a
fallback would have meant shipping two ranking modes with silently different
trustworthiness. The script now:
- Sends one POST to the Apify actor per run, with `keywords`, `maxItems`, `sortBy`,
  and a `datePosted` bucket mapped from `--days`.
- Gets back structured JSON with real `stats.reactions/comments/reposts/impressions`
  already computed by the actor — no scraping, parsing, or extraction logic needed on
  our side at all.
- Caches the full response by a hash of the request payload, so re-running an
  identical query is free (`--force-refresh` bypasses this deliberately, since
  bypassing = re-billing).

**Verified against the live endpoint before calling it done**: ran the script with an
invalid token and confirmed a real `401 Unauthorized` from Apify (proves the actor ID
and URL path are correct — a typo'd actor ID would 404, not 401). Did not spend real
money verifying a successful call, since that requires the user's own paid/free-tier
token.

### 3.7 Cost pivot: free tier instead of paid
User: *"we can not pay for it, find a free one."* Researched Apify's pricing tiers
directly rather than assuming — found Apify's **Free plan is permanent, requires no
credit card, and grants $5 of platform credit every billing cycle** (resets monthly,
doesn't roll over). At $1.50/1,000 posts, that's roughly **3,000 real posts/month at
$0 out of pocket**.

This was presented against the two genuinely-free alternatives (own-account browser
automation; public-search-with-no-real-numbers) as an explicit three-way tradeoff —
free credit doesn't touch the user's LinkedIn account *and* still returns real
numbers, it's just capped. The user chose the free-credit path.

**No code changed for this decision** — the script already reads
`APIFY_API_TOKEN` from the environment; a free-plan token works identically to a
paid one. The only difference is which token gets exported, and that the monthly
cap ($5 → ~3,000 posts) is now a real ceiling worth watching in Apify's console under
Usage.

## 4. Design decisions and why (quick reference)

| Decision | Chosen | Rejected | Why |
|---|---|---|---|
| Discovery + engagement source | Apify managed-pool actor | DDG HTML scraping | DDG returns a bot-detection challenge to scripted requests; not fixable without evasion techniques that weren't worth pursuing |
| LinkedIn account exposure | Zero — actor uses its own pool | Browser automation on user's own account | User's personal LinkedIn account should never be put at ban risk for this |
| Cost model | Apify Free plan ($5/mo credit, no card) | Paid Apify usage | User can't pay; free tier covers realistic volume (~3,000 posts/mo) |
| Engagement ranking | Composite score, comments/reposts weighted above likes | Raw reaction count only / engagement rate | Comments and reposts signal deeper engagement than a like; engagement-rate needs follower data the actor doesn't reliably provide |
| Schema source of truth | Apify's live build API (`/v2/acts/.../builds/<id>`) | Trusting a summarized marketing page | Caught a real field-value mismatch (`"24h"` vs `"24_hours"`) before it shipped |
| Recency filtering | Mapped to actor's fixed buckets (`24h`/`week`/`month`) | Arbitrary day counts | Actor only supports those three buckets plus "any time" — no finer granularity exists upstream |
| Result caching | Hash of full request payload, on disk, `--force-refresh` to bypass | No caching / TTL-based caching | Every uncached call costs real money (even on the free tier, against the monthly cap) — re-running the same query by accident shouldn't re-charge |
| Output format | `.xlsx` (color-coded by data quality) or `.csv` | JSON only | Matches the existing `job-scout/` tool's output convention in this workspace; directly usable without further processing |
| Scheduling | Not built in | Cron/anvi `/schedule` integration | User said they have scheduling handled themselves — this script stays a single invocable unit |

## 5. File layout

```
linkedin-scout/
├── linkedin_scout.py   # entire pipeline — CLI, Apify call, scoring, output writers
├── pyproject.toml      # uv project (package=false, flat script), deps: httpx, openpyxl
├── README.md           # user-facing usage docs
├── ARCHITECTURE.md      # this file
├── .gitignore           # .venv/, output/cache/, output/*.xlsx, output/*.csv
└── output/
    ├── cache/            # apify_<sha256>.json — raw cached actor responses
    └── <topic-slug>.xlsx # generated results (gitignored)
```

## 6. Known limitations (by design, not oversight)

- **One topic per run.** The actor supports multiple `keywords` in one call
  (de-duplicated results), but the CLI only exposes a single `--topic` — matches what
  was actually asked for; trivial to extend if multi-topic runs are needed later.
- **Recency is bucketed, not exact.** `--days 10` collapses to the `month` bucket
  (the actor has no `10-day` option) — the script maps to the nearest bucket rather
  than pretending to a precision that doesn't exist upstream.
- **300-second sync timeout.** Very large `--limit` values on a slow query could hit
  Apify's sync-endpoint timeout; no async/poll fallback is implemented since typical
  runs (tens of posts) finish well within that window.
- **`data_quality` can still be `partial`/`none`.** The actor is expected to return
  real numbers most of the time (that's its whole purpose), but LinkedIn's own result
  availability varies run to run — the quality flag exists precisely so a low-quality
  result is visible rather than silently trusted.
