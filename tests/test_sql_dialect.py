"""
Guards against SQL the production database cannot run.

The suite runs on in-memory SQLite (conftest.py) while production runs MySQL.
That gap is not cosmetic: `.nullslast()` emits PostgreSQL's NULLS LAST, SQLite
has accepted it since 3.30, and MySQL rejects it with error 1064 — so
/analytics/outcomes returned 500 on every dashboard load while 268 tests stayed
green.

This is a grep, and a grep is not a substitute for running the suite against
MySQL. It catches this exact class — a dialect-only construct reaching the
query layer — and it cannot catch semantic differences between engines. The
real fix is a MySQL-backed test run; until that exists, this is the guard, and
saying otherwise would repeat the mistake it exists to prevent.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parent.parent / "app"

# Construct -> the portable way to say the same thing.
FORBIDDEN = {
    r"\.nullslast\(": "order_by(col.is_(None), col.desc()) — MySQL has no NULLS LAST",
    r"\.nullsfirst\(": "order_by(col.isnot(None), col.desc()) — MySQL has no NULLS FIRST",
    r"\bdistinct_on\b": "DISTINCT ON is PostgreSQL only; use a window function or a subquery",
    r"\barray_agg\b": "array_agg is PostgreSQL only; aggregate in Python",
    r"\bON CONFLICT\b": "ON CONFLICT is PostgreSQL only; MySQL spells it ON DUPLICATE KEY UPDATE",
    r"\bRETURNING\b": "RETURNING is not supported by MySQL; re-select after the write",
}


def _python_sources() -> list[Path]:
    return [p for p in APP.rglob("*.py") if "__pycache__" not in p.parts]


@pytest.mark.parametrize("pattern, remedy", list(FORBIDDEN.items()))
def test_no_dialect_specific_sql_reaches_the_query_layer(pattern, remedy):
    compiled = re.compile(pattern)
    offenders: list[str] = []

    for path in _python_sources():
        for number, line in enumerate(path.read_text().splitlines(), start=1):
            code = line.split("#", 1)[0]          # the remedy is named in comments
            if compiled.search(code):
                offenders.append(f"{path.relative_to(APP.parent)}:{number}")

    assert not offenders, (
        f"{pattern} is not portable to MySQL, which production runs.\n"
        f"  Use: {remedy}\n"
        f"  Found at: {', '.join(offenders)}"
    )


def test_the_guard_actually_matches_the_bug_it_was_written_for():
    """A check that has never been seen to fire is not yet a check."""
    assert re.compile(r"\.nullslast\(").search("order_by(Post.published_time.desc().nullslast())")
