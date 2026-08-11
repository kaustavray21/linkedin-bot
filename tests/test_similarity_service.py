from __future__ import annotations

from app.core.config import settings
from app.services.similarity_service import (
    check_hashtag_overlap,
    check_similarity,
    jaccard_similarity,
    longest_common_word_run,
)

SOURCE = """I failed twice before I learned anything useful about shipping.

The first time I blamed the tools. The second time I blamed the timeline.

Neither was the real problem. I was building things nobody had asked for.

#BuildInPublic #Startups #Lessons"""


def test_verbatim_copy_is_rejected():
    report = check_similarity(SOURCE, SOURCE)
    assert not report.passed
    assert report.jaccard > 0.9
    assert report.band == "rejected"


def test_genuine_paraphrase_passes():
    paraphrase = """Two early ventures taught me more than any course could.

At first I pointed at my stack. Later I pointed at the calendar.

Both excuses missed it. Nobody wanted what I was making.

#ShippingLoud #FounderLife #HardWon"""
    report = check_similarity(paraphrase, SOURCE)
    assert report.passed, report.reason
    assert report.jaccard < settings.similarity_jaccard_max


def test_one_lifted_sentence_trips_the_word_run_check():
    """The case Jaccard alone misses: mostly original text with a single
    sentence copied straight out of the source."""
    lifted = """Completely different opening about a different subject entirely.

I was building things nobody had asked for.

An unrelated closing thought that shares no vocabulary with the original.

#Alpha #Beta"""
    report = check_similarity(lifted, SOURCE)
    assert not report.passed
    assert report.longest_run >= settings.similarity_max_word_run
    assert "verbatim run" in report.reason


def test_word_run_finds_the_actual_shared_phrase():
    run_len, run_text = longest_common_word_run(
        "totally new words here I was building things nobody had asked for and more",
        SOURCE,
    )
    assert run_len >= 8
    assert "building things nobody had asked for" in run_text


def test_disjoint_text_scores_zero():
    assert jaccard_similarity("alpha beta gamma delta", "one two three four") == 0.0


def test_short_inputs_do_not_crash():
    assert jaccard_similarity("hi", "hi") == 0.0        # too short for a trigram
    assert longest_common_word_run("", "anything") == (0, "")


def test_band_reports_green_amber_rejected():
    assert check_similarity(SOURCE, SOURCE).band == "rejected"
    assert check_similarity("nothing at all in common here friend", SOURCE).band == "green"


def test_generic_hashtags_are_not_counted_as_copying():
    """#AI is the name of the topic, not a stolen coinage."""
    copied = check_hashtag_overlap(["#AI", "#Python"], ["#AI", "#Python"])
    assert copied == []


def test_distinctive_hashtag_reuse_is_flagged():
    copied = check_hashtag_overlap(
        ["#BuildInPublic", "#Unrelated"], ["#BuildInPublic", "#Startups"]
    )
    assert copied == ["#BuildInPublic"]
