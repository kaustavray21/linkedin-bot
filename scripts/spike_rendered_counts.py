"""
scripts/spike_rendered_counts.py

S1 — the reaction-count spike.

The question this answers: LinkedIn hydrates parts of a post page client-side,
so does rendering the page in a real browser expose engagement counts that a
plain HTTP fetch misses? If it does, a second "rendered tier" is worth building
for the posts the user filters by or clones. If it does not, the tier is cost
with no return.

It measures three tiers against the same URLs and the same extraction code:

  plain-parser   what ships today — httpx + parser.parse_post()
  plain-attrs    the same httpx response, read with the attribute extractor below
  rendered       headless Chrome over CDP, then both extractors again

Run:  bot-env/bin/python -m scripts.spike_rendered_counts --rendered 8
      bot-env/bin/python -m scripts.spike_rendered_counts --rendered 0   # no browser

Verdict as measured on 2026-08-18 — rendering gained nothing on 8/8 readable
pages and rescued 0/3 unreadable ones, at 7x the wall-clock and ~800 MB. Full
write-up: ~/.anvideck/projects/linkedin-bot/ref/GROUND_TRUTH_LINKEDIN_PUBLIC_POST_COUNTS.md

Live requests go to linkedin.com from this machine's IP, unauthenticated and
without cookies, paced by the same requests-per-second knob discovery uses.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import statistics
import sys
import time
from dataclasses import asdict, dataclass

sys.path.insert(0, ".")

from sqlalchemy import select

from app.database.connection import get_session_factory
from app.database.models import DiscoveredPost
from app.services.discovery.egress.base import FetchResult
from app.services.discovery.egress.strategies import DirectEgress
from app.services.discovery.parser import parse_post

# --------------------------------------------------------------------- extractor

# The post's own counts live in data-* attributes on the social-actions anchors,
# not in the embedded JSON the shipping parser greps. Anchored on data-test-id
# rather than class names because the classes are Tailwind-ish utility soup that
# changes with every redesign, while the test ids have been stable.
ATTR_PATTERNS = {
    "reactions": re.compile(r'data-num-reactions="(\d+)"'),
    "comments": re.compile(r'data-num-comments="(\d+)"'),
    "reposts": re.compile(r'data-num-(?:reposts|shares)="(\d+)"'),
}


def extract_attr_counts(html: str) -> dict[str, int | None]:
    """Post-level counts from the social-actions attributes.

    A missing attribute stays None. The whole point of the exercise is telling
    "could not read it" apart from "it is zero".
    """
    out: dict[str, int | None] = {"reactions": None, "comments": None, "reposts": None}
    for key, pattern in ATTR_PATTERNS.items():
        m = pattern.search(html)
        if m:
            out[key] = int(m.group(1))
    return out


BLOCK_MARKERS = ("authwall", "join now to see", "sign in to view", "please log in")


def looks_blocked(status: int | None, html: str) -> bool:
    if status in (429, 999, 403):
        return True
    head = (html or "")[:4000].lower()
    # A sign-in prompt in the chrome of a readable page is normal; only treat it
    # as a block when the post body did not come through at all.
    return any(m in head for m in BLOCK_MARKERS) and "data-num-reactions" not in html


@dataclass
class Row:
    url: str
    tier: str
    status: int | None
    elapsed: float
    bytes: int
    blocked: bool
    parser_reactions: int | None
    parser_comments: int | None
    parser_reposts: int | None
    attr_reactions: int | None
    attr_comments: int | None
    attr_reposts: int | None
    layer: str
    content_chars: int


def measure(url: str, tier: str, status: int | None, html: str, elapsed: float) -> Row:
    parsed = parse_post(
        FetchResult(url=url, status_code=status or 0, content=html, content_kind="html")
    )
    attrs = extract_attr_counts(html)
    return Row(
        url=url,
        tier=tier,
        status=status,
        elapsed=round(elapsed, 2),
        bytes=len(html),
        blocked=looks_blocked(status, html),
        parser_reactions=parsed.reactions,
        parser_comments=parsed.comments,
        parser_reposts=parsed.reposts,
        attr_reactions=attrs["reactions"],
        attr_comments=attrs["comments"],
        attr_reposts=attrs["reposts"],
        layer=parsed.layer,
        content_chars=len(parsed.content_text or ""),
    )


# ------------------------------------------------------------------------ tiers


async def fetch_plain(url: str) -> Row:
    t0 = time.monotonic()
    try:
        res = await DirectEgress().fetch(url)
    except Exception as exc:  # transport failure is a result, not a crash
        return measure(url, "plain", None, f"__error__ {exc}", time.monotonic() - t0)
    return measure(url, "plain", res.status_code, res.content, time.monotonic() - t0)


async def fetch_rendered(browser, url: str, settle: float) -> Row:
    from scripts._cdp import Page

    t0 = time.monotonic()
    async with Page(browser) as page:
        nav = await page.goto(url, settle=settle)
        html = await page.html()
    return measure(url, "rendered", nav["status"], html, time.monotonic() - t0)


# ------------------------------------------------------------------------ report


def hit_rate(rows: list[Row], field: str) -> tuple[int, int]:
    got = sum(1 for r in rows if getattr(r, field) is not None)
    return got, len(rows)


def pct(got: int, total: int) -> str:
    return f"{got}/{total} ({100.0 * got / total:.0f}%)" if total else "0/0"


def report(plain: list[Row], rendered: list[Row]) -> None:
    print("\n" + "=" * 72)
    print("S1 — reaction-count spike")
    print("=" * 72)

    print(f"\nPLAIN HTTP  (n={len(plain)})")
    for field, label in (
        ("parser_reactions", "reactions via shipping parser"),
        ("attr_reactions", "reactions via data-attributes"),
        ("parser_comments", "comments  via shipping parser"),
        ("attr_comments", "comments  via data-attributes"),
        ("attr_reposts", "reposts   via data-attributes"),
    ):
        print(f"  {label:34s} {pct(*hit_rate(plain, field))}")
    blocked = sum(1 for r in plain if r.blocked)
    print(f"  {'blocked / authwalled':34s} {pct(blocked, len(plain))}")
    times = [r.elapsed for r in plain]
    if times:
        print(f"  {'median wall-clock':34s} {statistics.median(times):.2f}s")

    if not rendered:
        print("\nRENDERED  — not run")
        return

    print(f"\nRENDERED (headless Chrome)  (n={len(rendered)})")
    for field, label in (
        ("parser_reactions", "reactions via shipping parser"),
        ("attr_reactions", "reactions via data-attributes"),
        ("attr_comments", "comments  via data-attributes"),
        ("attr_reposts", "reposts   via data-attributes"),
    ):
        print(f"  {label:34s} {pct(*hit_rate(rendered, field))}")
    blocked = sum(1 for r in rendered if r.blocked)
    print(f"  {'blocked / authwalled':34s} {pct(blocked, len(rendered))}")
    times = [r.elapsed for r in rendered]
    print(f"  {'median wall-clock':34s} {statistics.median(times):.2f}s")

    # The decision number: pages where rendering read a count plain could not.
    by_url = {r.url: r for r in plain}
    gained = disagreed = compared = 0
    for r in rendered:
        p = by_url.get(r.url)
        if not p:
            continue
        compared += 1
        if r.attr_reactions is not None and p.attr_reactions is None:
            gained += 1
        if (r.attr_reactions is not None and p.attr_reactions is not None
                and r.attr_reactions != p.attr_reactions):
            disagreed += 1

    print("\nDELTA — what rendering bought")
    print(f"  {'pages compared':34s} {compared}")
    print(f"  {'counts ONLY rendering could read':34s} {pct(gained, compared)}")
    print(f"  {'counts that disagreed':34s} {pct(disagreed, compared)}")


# -------------------------------------------------------------------------- main


async def load_urls(limit: int) -> list[str]:
    async with get_session_factory()() as s:
        rows = (
            await s.execute(
                select(DiscoveredPost.post_url)
                .where(DiscoveredPost.purged_at.is_(None))
                .order_by(DiscoveredPost.fetched_at.desc())
                .limit(limit)
            )
        ).scalars().all()
    return list(rows)


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=35, help="URLs for the plain tier")
    ap.add_argument("--rendered", type=int, default=8, help="subset to also render")
    ap.add_argument("--rps", type=float, default=2.0, help="requests/second, plain tier")
    ap.add_argument("--settle", type=float, default=6.0, help="post-load hydration wait")
    ap.add_argument("--out", default=None, help="write raw rows here as JSON")
    args = ap.parse_args()

    urls = await load_urls(args.limit)
    print(f"{len(urls)} URLs from discovered_posts")

    print(f"\nplain tier at {args.rps} req/s ...")
    plain: list[Row] = []
    for i, u in enumerate(urls, 1):
        plain.append(await fetch_plain(u))
        r = plain[-1]
        print(f"  [{i:2d}/{len(urls)}] {r.status} attrs_r={r.attr_reactions} "
              f"parser_r={r.parser_reactions} blocked={r.blocked}")
        if i < len(urls):
            await asyncio.sleep(1.0 / args.rps)

    rendered: list[Row] = []
    if args.rendered > 0:
        from scripts._cdp import Browser

        subset = urls[: args.rendered]
        print(f"\nrendered tier, {len(subset)} pages, serial ...")
        browser = Browser()
        await browser.start()
        try:
            for i, u in enumerate(subset, 1):
                try:
                    row = await fetch_rendered(browser, u, args.settle)
                except Exception as exc:
                    print(f"  [{i}] FAILED {exc}")
                    continue
                rendered.append(row)
                print(f"  [{i:2d}/{len(subset)}] {row.status} {row.elapsed:5.1f}s "
                      f"attrs_r={row.attr_reactions} rss={browser.rss_kb() // 1024}MB")
        finally:
            peak = browser.rss_kb()
            await browser.stop()
            print(f"  browser tree RSS at end: {peak // 1024} MB")

    report(plain, rendered)

    if args.out:
        with open(args.out, "w") as fh:
            json.dump(
                {"plain": [asdict(r) for r in plain],
                 "rendered": [asdict(r) for r in rendered]},
                fh, indent=2,
            )
        print(f"\nraw rows → {args.out}")


if __name__ == "__main__":
    asyncio.run(main())
