# LinkedIn Auto-Posting Bot 🤖✨

An AI-powered LinkedIn campaign publisher and scheduler. This application features a beautiful, dark-themed, glassmorphic Single-Page Application (SPA) frontend that allows you to generate post copy, generate images using Gemini AI, and schedule posts to publish automatically.

---

## 🚀 Features

- **OAuth 2.0 Auth**: Secure, direct authentication with LinkedIn's developer portal.
- **Glassmorphic SPA Dashboard**: Visual interface for discovery, posting, scheduling, and history.
- **Topic Discovery**: Give it a topic and it finds top-performing public LinkedIn posts on that subject — free, keyless, and **without ever using your LinkedIn account** (see *Discovery & Safety* below).
- **One-Click Drafting**: `Find & draft automatically` runs the whole pipeline — discover, rank, clone the structure, remix the hashtags, generate a matching image.
- **Structure Cloning**: Reproduces an exemplar post's exact paragraph and line rhythm instead of averaging it away. If a creator leaves a two-word line alone on its own row, so does your draft.
- **Originality Gate**: Every generated draft is scored against its exemplar (trigram overlap + longest verbatim run) and regenerated or refused if it lands too close. Thresholds are tunable — see `scripts/tune_similarity.py`.
- **Your Own Images**: Upload from your computer or fetch from any web URL, alongside AI generation.
- **Visual Style Matching**: Describes a high-performing post's image with Gemini vision, then generates an **original** image in that visual language — never republishing the source graphic.
- **fal.ai Image Generator**: Premium image generation using the **Nano Banana 2** model via fal.ai.
- **Style Wizard**: Curate reference posts locally and generate content matched to their structure.
- **Media Post Support**: Automated upload of images to the LinkedIn Media API for image-rich updates.
- **Automated Scheduler**: Background APScheduler job that polls every minute and publishes due scheduled posts.
- **Robust Structured Logging**: Loguru configuration with automatic request tracking that prevents formatting crashes.

---

## 🛠️ Quick Start

### 1. Prerequisites
- **Python 3.12+**
- **MySQL 8+** (with a schema created, e.g., `linkedin_bot`)
- **LinkedIn Developer Account & Application** (with the *Share on LinkedIn* and *Sign In with LinkedIn* products added)

---

## 🚨 Troubleshooting OAuth Errors

### 1. "redirect_uri does not match"
If you see the error **"Bummer, something went wrong. The redirect_uri does not match the registered value"** when trying to sign in, it means the URL the application is sending does not match what LinkedIn expects.

**How to Fix:**
1. Open the [LinkedIn Developer Portal](https://www.linkedin.com/developers/apps).
2. Select your application.
3. Go to the **Auth** tab.
4. Scroll down to the **OAuth 2.0 Settings** section.
5. Under **Authorized redirect URLs for your app**, click **Add redirect URL** and add:
   ```
   http://localhost:8000/auth/callback
   ```
   *(If you access the application using `127.0.0.1` instead, also add `http://127.0.0.1:8000/auth/callback`)*
6. Click **Update** to save.
7. Ensure your local `.env` file matches exactly:
   ```env
   REDIRECT_URI=http://localhost:8000/auth/callback
   ```

### 2. "Scope 'openid' is not authorized"
If you see the error **"Scope 'openid' is not authorized for your application"** when logging in, it means your LinkedIn application does not have the permissions requested by the OIDC login flow.

**How to Fix:**
1. Open the [LinkedIn Developer Portal](https://www.linkedin.com/developers/apps) and select your app.
2. Go to the **Products** tab.
3. Find the product named **"Sign In with LinkedIn using OpenID Connect"** (not the legacy "Sign In with LinkedIn" product).
4. Click **Request access** or **Add** to enable it for your app.
5. In addition, ensure **"Share on LinkedIn"** is added under the **Products** tab to authorize the `w_member_social` posting permission.
6. Once access is approved (usually instant), retry signing in from your local application.

---

## 📥 Installation and Setup

### 1. Clone the project and configure environment variables
Copy the example environment file and open it for editing:
```bash
cp .env.example .env
```

Configure the following variables in `.env`:
```env
# Database Credentials
MYSQL_HOST=127.0.0.1
MYSQL_PORT=3306
MYSQL_DATABASE=linkedin_bot
MYSQL_USER=root
MYSQL_PASSWORD=your_mysql_password_here

# LinkedIn API Client
CLIENT_ID=your_linkedin_client_id
CLIENT_SECRET=your_linkedin_client_secret
REDIRECT_URI=http://localhost:8000/auth/callback
APP_URL=http://localhost:8000

# Google Gemini API
GEMINI_API_KEY=your_gemini_api_key
GEMINI_MODEL=gemini-2.0-flash
GEMINI_IMAGE_MODEL=gemini-3.1-flash-lite-image

# fal.ai API
FAL_API_KEY=your_fal_api_key
FAL_IMAGE_MODEL=fal-ai/nano-banana-2
```

Optional — discovery, safety limits, and originality. Every one of these has a
working default, so you can leave the whole block out:

```env
# Which search backend finds candidate posts. No API key, no cost.
DISCOVERY_PROVIDER=ddg              # ddg | searxng | manual
SEARXNG_URL=http://localhost:8080   # only if DISCOVERY_PROVIDER=searxng

# How post-page fetches leave this machine.
# `direct` returns raw HTML and so extracts the most data — see Discovery & Safety.
DISCOVERY_EGRESS=direct             # direct | jina | proxy
DISCOVERY_EGRESS_FALLBACK=direct
DISCOVERY_PROXY_URL=

# Rate limiting. These are safety limits, not tuning knobs — raising them
# increases the chance of being IP-blocked.
DISCOVERY_MIN_INTERVAL_SECONDS=30
DISCOVERY_DAILY_FETCH_CAP=40
DISCOVERY_CIRCUIT_THRESHOLD=3
DISCOVERY_CIRCUIT_COOLDOWN_HOURS=24

# How long fetched post content is kept before it is stripped back to an
# anonymous structural fingerprint.
DISCOVERY_RETENTION_DAYS=90

# Originality gate. Lower = stricter = more regenerations.
SIMILARITY_JACCARD_MAX=0.25
SIMILARITY_MAX_WORD_RUN=8
SIMILARITY_MAX_RETRIES=2
```

---

## 🔒 Discovery & Safety

Discovery is built so that **your LinkedIn account is never the identity that
reads post data**.

- Search runs against DuckDuckGo (or a self-hosted SearXNG). That step never
  touches LinkedIn at all.
- Post pages are then fetched **unauthenticated** — no cookie, no session, no
  `Authorization` header. LinkedIn publishes structured data (JSON-LD and Open
  Graph tags) on public post pages before the sign-in wall applies, which is
  what makes this work without an account.
- Your OAuth token is used **only** for publishing. `LinkedInService` has no
  read or search methods at all.

Because the reads are unauthenticated, the worst case is an IP-level rate limit
— there is no account attached to restrict. The limits above keep request volume
at roughly the pace of a person reading a few posts:

| Guard | Behaviour |
|---|---|
| Pacing | ≥30s jittered gap between fetches |
| Daily cap | 40 fetches, enforced across **all** egress strategies |
| Circuit breaker | Opens for 24h after 3 consecutive blocks, **per strategy** |
| Cache | A post is fetched exactly once, ever — `post_url` is unique |

One honest caveat: with `DISCOVERY_EGRESS=direct`, fetches originate from the
same IP you browse LinkedIn from. Logged-out traffic does not get accounts
banned, but if you want to decouple entirely, set `DISCOVERY_EGRESS=jina` (routes
via `r.jina.ai`) or `proxy`. Note that `jina` returns rendered markdown, which
strips the `<script>` tags carrying JSON-LD — so it extracts **less** than
`direct`. That is why `direct` is the default.

**Nothing copies.** Generated posts clone structure, not wording; hashtags are
regenerated rather than reused (common industry tags like `#AI` excepted); and
reference images are described for style, never republished.

### Tuning the originality gate

```bash
python scripts/tune_similarity.py --profile sub1 --runs 3
```

Prints the exemplar's shape, each generated post's shape, whether they match,
and the similarity distribution — so you can set the threshold from real output
rather than guessing.

### 2. Set up the Python virtual environment
Activate your environment and install dependencies:
```bash
source bot-env/bin/activate
pip install -r requirements.txt
```

### 3. Run Database Migrations
Deploy the database schema using Alembic:
```bash
alembic upgrade head
```

### 4. Start the Application Server
Run the FastAPI development server:
```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```
Your application will be live at 👉 **[http://localhost:8000/](http://localhost:8000/)**

---

## 🖥️ Using the Application

1. **Dashboard Overview**: Track your counts of successfully `Published`, `Scheduled`, and `Failed` posts. See quick upcoming posts list.
2. **Discover**:
   - Enter a topic and hit **Find & draft automatically** — the bot finds top posts, picks the best one, clones its structure, remixes its hashtags, generates a matching image, and drops the draft into Create for review.
   - Or hit **Find posts** to browse results first, then draft from a specific one.
   - Each card shows whether its ranking is based on **measured** engagement or **relevance** — the app never presents a guess as a metric.
   - **Delete** removes the post's content while keeping its anonymous structural fingerprint, so drafts already built from it stay reproducible.
3. **Create Post Wizard**:
   - Use the **AI Copywriter** to generate post drafts by providing a topic.
   - Add an image via **AI Generate**, **Upload** (from your computer), or **From URL**. An image is always offered but never required.
   - Toggle between **Publish Now** and **Schedule for Later** (select date/time).
4. **Local Reference Styles** (inside Create Post):
   - Pick a reference creator profile (`sub1`, `sub2`, or blended `combined`) from your own curated `.txt` files in `app/references/`.
   - `combined` picks the single most *typical* post from the set rather than averaging the set's numbers — averaging is what used to produce uniform, generic paragraphing.
   - Enter a topic and optional notes to generate a draft matching that post's structure.
5. **Publication History**: Manage, refresh, publish drafts immediately, and delete historical logs.

