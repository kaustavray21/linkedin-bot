"""
tests/test_reference_loader.py
"""

from pathlib import Path
import pytest

from app.services.reference_loader import load_reference_profiles, load_all_posts, get_profile


@pytest.fixture
def fake_references_dir(tmp_path):
    # Setup sub1
    sub1 = tmp_path / "sub1"
    sub1.mkdir()
    (sub1 / "linkind_id.json").write_text('{"profile_url": "https://linkedin.com/in/user1"}', encoding="utf-8")
    (sub1 / "ref-1.txt").write_text("Hello from post 1", encoding="utf-8")
    (sub1 / "ref-2.txt").write_text("Hello from post 2", encoding="utf-8")

    # Setup sub2
    sub2 = tmp_path / "sub2"
    sub2.mkdir()
    (sub2 / "linkedin_id.json").write_text('{"url": "https://linkedin.com/in/user2"}', encoding="utf-8")
    (sub2 / "ref-1.txt").write_text("Hello from post 3", encoding="utf-8")

    return tmp_path


def test_load_reference_profiles(fake_references_dir):
    profiles = load_reference_profiles(fake_references_dir)
    assert len(profiles) == 2
    
    p1 = profiles[0]
    assert p1.slug == "sub1"
    assert p1.profile_url == "https://linkedin.com/in/user1"
    assert p1.post_count == 2
    assert p1.posts == ["Hello from post 1", "Hello from post 2"]

    p2 = profiles[1]
    assert p2.slug == "sub2"
    assert p2.profile_url == "https://linkedin.com/in/user2"
    assert p2.post_count == 1
    assert p2.posts == ["Hello from post 3"]


def test_load_all_posts(fake_references_dir):
    posts = load_all_posts(fake_references_dir)
    assert len(posts) == 3
    assert posts == ["Hello from post 1", "Hello from post 2", "Hello from post 3"]


def test_get_profile(fake_references_dir):
    profile = get_profile("sub1", fake_references_dir)
    assert profile is not None
    assert profile.slug == "sub1"

    none_profile = get_profile("nonexistent", fake_references_dir)
    assert none_profile is None
