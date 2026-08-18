"""
scripts/backfill_post_counts.py

Re-read engagement counts for stored discovered posts.

Rows fetched before the count extractor was fixed carry `reactions = NULL` on
every post — the old parser searched for JSON keys that do not exist on a
LinkedIn page — and a `comments` value that came from the JSON-LD
`commentCount`, which reports the length of the inlined comment[] array and is a
false zero on roughly a fifth of posts. Both are re-read here from the markup.

Dry by default. Nothing is written without --apply.

    bot-env/bin/python -m scripts.backfill_post_counts            # show the diff
    bot-env/bin/python -m scripts.backfill_post_counts --apply    # write it

Live unauthenticated requests, paced by --rps. Purged rows are skipped: their
content was deleted on purpose and re-fetching would repopulate it.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

sys.path.insert(0, ".")

from sqlalchemy import select

from app.database.connection import get_session_factory
from app.database.models import DiscoveredPost
from app.services.discovery.egress.strategies import DirectEgress
from app.services.discovery.parser import parse_post
from app.services.discovery.ranking import compute_score, describe_basis


def _fmt(value: int | None) -> str:
    return "—" if value is None else str(value)


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="write the changes")
    ap.add_argument("--rps", type=float, default=2.0)
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()

    factory = get_session_factory()
    async with factory() as session:
        query = select(DiscoveredPost).where(DiscoveredPost.purged_at.is_(None))
        if args.limit:
            query = query.limit(args.limit)
        posts = (await session.execute(query)).scalars().all()

        print(f"{len(posts)} unpurged rows\n")
        print(f"{'id':>4}  {'reactions':>18}  {'comments':>18}  {'basis':>22}  status")
        print("-" * 88)

        changed = failed = 0

        for i, post in enumerate(posts):
            try:
                result = await DirectEgress().fetch(post.post_url)
            except Exception as exc:
                print(f"{post.id:>4}  fetch failed: {exc}")
                failed += 1
                continue

            if not result.ok:
                print(f"{post.id:>4}  HTTP {result.status_code} — skipped")
                failed += 1
                continue

            parsed = parse_post(result)

            # A page that no longer parses at all is a signal to investigate, not
            # a reason to erase counts that were read successfully before.
            if not parsed.has_content and parsed.reactions is None:
                print(f"{post.id:>4}  no content and no counts — left untouched")
                failed += 1
                continue

            # Captured before the write below, so --apply prints the real
            # before/after rather than "new → new".
            was = (post.reactions, post.comments, post.metrics_source)

            new_basis = describe_basis(parsed.reactions, parsed.comments, parsed.reposts)
            moved = (
                post.reactions != parsed.reactions
                or post.comments != parsed.comments
                or post.metrics_source != new_basis
            )

            mark = ""
            if moved:
                changed += 1
                mark = "UPDATED" if args.apply else "would update"
                if args.apply:
                    post.reactions = parsed.reactions
                    post.comments = parsed.comments
                    post.reposts = parsed.reposts
                    post.metrics_source = new_basis
                    post.engagement_score = compute_score(
                        reactions=parsed.reactions,
                        comments=parsed.comments,
                        reposts=parsed.reposts,
                        serp_rank=post.serp_rank,
                        posted_at=post.posted_at,
                        query_overlap=post.query_overlap,
                    )

            print(
                f"{post.id:>4}  "
                f"{_fmt(was[0]):>8} → {_fmt(parsed.reactions):<7}  "
                f"{_fmt(was[1]):>8} → {_fmt(parsed.comments):<7}  "
                f"{was[2]:>10} → {new_basis:<9}  {mark}"
            )

            if i < len(posts) - 1:
                await asyncio.sleep(1.0 / args.rps)

        if args.apply:
            await session.commit()
            print(f"\ncommitted — {changed} rows updated, {failed} skipped")
        else:
            print(f"\ndry run — {changed} rows would change, {failed} skipped")
            print("re-run with --apply to write")


if __name__ == "__main__":
    asyncio.run(main())
