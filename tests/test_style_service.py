"""
tests/test_style_service.py

Lives in the top-level tests/ folder alongside test_auth.py and
test_health.py, not inside app/ — matches the existing project convention.
"""

import pytest

from app.services.style_service import extract_style_profile


def test_extract_style_profile_basic_counts():
    posts = [
        "Is your career actually moving forward?\n\nHere's what I learned.\n\n#careertips #growth",
        "3 lessons from my first year in tech.\n\nRead this before you quit.\n\n#tech #career",
    ]
    profile = extract_style_profile(posts)

    assert profile.sample_count == 2
    assert profile.avg_hashtag_count == 2.0
    assert "#careertips" in profile.common_hashtags or "#career" in profile.common_hashtags


def test_hook_style_detects_question():
    posts = ["Is this the biggest mistake in your career?\n\nLet's talk about it."]
    profile = extract_style_profile(posts)
    assert profile.hook_style == "question"


def test_hook_style_detects_stat_or_number():
    posts = ["3 things nobody tells you about leadership.\n\nHere they are."]
    profile = extract_style_profile(posts)
    assert profile.hook_style == "stat_or_number"


def test_cta_pattern_detected():
    posts = [
        "Some thoughts on hiring.\n\nIt's harder than people think.\n\nWhat do you think?",
        "Another post.\n\nMore content here.\n\nLet me know your experience in the comments.",
    ]
    profile = extract_style_profile(posts)
    assert profile.has_cta_pattern is True


def test_raises_on_empty_input():
    with pytest.raises(ValueError):
        extract_style_profile([])
