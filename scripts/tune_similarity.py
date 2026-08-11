"""
scripts/tune_similarity.py

Sets the similarity thresholds from data instead of guesswork, and shows whether
a generated post actually reproduced its exemplar's shape.

Usage:
    python -m scripts.tune_similarity --runs 3
    python -m scripts.tune_similarity --profile sub1 --topic "shipping fast"
    python -m scripts.tune_similarity --dry-run          # no API calls

Reports, per run: the exemplar's block/line shape, the generated post's shape,
whether they match, and the similarity scores. Then prints the score
distribution so SIMILARITY_JACCARD_MAX can be chosen against real output.
"""

from __future__ import annotations

import argparse
import asyncio
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import settings  # noqa: E402
from app.services.content_generation_service import (  # noqa: E402
    generate_with_layout,
    select_representative,
)
from app.services.layout_service import extract_skeleton  # noqa: E402
from app.services.similarity_service import check_similarity  # noqa: E402
from app.services.style_service import extract_style_profile  # noqa: E402

REFERENCES_DIR = Path(__file__).resolve().parents[1] / "app" / "references"


def load_posts(profile: str | None) -> list[str]:
    posts: list[str] = []
    directories = (
        [REFERENCES_DIR / profile] if profile else sorted(p for p in REFERENCES_DIR.iterdir() if p.is_dir())
    )
    for directory in directories:
        if not directory.exists():
            raise SystemExit(f"No such reference profile: {directory}")
        for txt in sorted(directory.glob("ref-*.txt")):
            text = txt.read_text(encoding="utf-8").strip()
            if text:
                posts.append(text)
    if not posts:
        raise SystemExit("No reference posts found.")
    return posts


def shape_of(text: str) -> str:
    """Render a post's structure as a compact signature, e.g. 1 | 1 | 3 | tags."""
    skeleton = extract_skeleton(text)
    parts = []
    for block in skeleton.blocks:
        if block.is_hashtag_block:
            parts.append("tags")
        else:
            parts.append(str(len(block.lines)))
    return " | ".join(parts)


def line_words(text: str) -> str:
    skeleton = extract_skeleton(text)
    return " | ".join(
        ",".join(str(line.words) for line in block.lines) for block in skeleton.blocks
    )


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--profile", default=None, help="e.g. sub1 (default: all)")
    parser.add_argument("--topic", default="what I learned shipping side projects")
    parser.add_argument("--dry-run", action="store_true", help="skip API calls")
    args = parser.parse_args()

    posts = load_posts(args.profile)
    exemplar = select_representative(posts)
    style = extract_style_profile(posts)
    skeleton = extract_skeleton(exemplar)

    print("=" * 72)
    print(f"Reference posts loaded : {len(posts)}")
    print(f"Chosen exemplar shape  : {shape_of(exemplar)}   (blocks | lines each)")
    print(f"Words per line         : {line_words(exemplar)}")
    print(f"Hashtags               : {skeleton.hashtag_count} ({skeleton.hashtag_placement})")
    print("=" * 72)
    print("\n--- EXEMPLAR ---")
    print(exemplar)

    if args.dry_run:
        print("\n[dry run — no API calls made]")
        return

    jaccards: list[float] = []
    runs_matching_shape = 0

    for run in range(args.runs):
        print("\n" + "=" * 72)
        print(f"RUN {run + 1}/{args.runs}")
        print("=" * 72)
        try:
            text, report = await generate_with_layout(
                topic=args.topic,
                exemplar=exemplar,
                user_notes="",
                style=style,
                variation_index=run % 3,
            )
        except ValueError as exc:
            print(f"REFUSED: {exc}")
            continue

        generated_shape = shape_of(text)
        matched = generated_shape == shape_of(exemplar)
        runs_matching_shape += int(matched)
        jaccards.append(report.jaccard)

        print(text)
        print("-" * 72)
        print(f"shape      : {generated_shape}   {'MATCH' if matched else 'DIFFERS'}")
        print(f"line words : {line_words(text)}")
        print(f"jaccard    : {report.jaccard:.4f}   (limit {settings.similarity_jaccard_max})")
        print(f"longest run: {report.longest_run} words (limit {settings.similarity_max_word_run})")
        print(f"band       : {report.band}")

    if jaccards:
        print("\n" + "=" * 72)
        print("DISTRIBUTION")
        print("=" * 72)
        print(f"runs                 : {len(jaccards)}")
        print(f"shape matched        : {runs_matching_shape}/{len(jaccards)}")
        print(f"jaccard min/med/max  : {min(jaccards):.4f} / {statistics.median(jaccards):.4f} / {max(jaccards):.4f}")
        headroom = settings.similarity_jaccard_max - max(jaccards)
        print(f"headroom to threshold: {headroom:+.4f}")
        if headroom < 0:
            print("  -> threshold is rejecting genuine output; consider raising it")
        elif headroom > 0.15:
            print("  -> threshold is loose; you could tighten it for more distinct output")


if __name__ == "__main__":
    asyncio.run(main())
