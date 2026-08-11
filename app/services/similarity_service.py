"""
app/services/similarity_service.py

Guards the line between "written in the same voice" and "copied".

Showing the model an exemplar post is what makes it reproduce a creator's
rhythm — but it also gives it the opportunity to lift phrasing wholesale. This
module measures how close the output landed and refuses anything that crossed
over, so the structural-cloning feature cannot quietly become plagiarism.

Two independent measures, because they fail differently:
  - Trigram Jaccard catches diffuse reuse — the same ideas in the same order
    with light paraphrasing, where no single sentence looks copied.
  - Longest common word-run catches concentrated reuse — one lifted sentence
    inside otherwise original text, which barely moves a Jaccard score.

A draft has to pass both.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.core.config import settings

WORD_RE = re.compile(r"[a-z0-9']+")


@dataclass
class SimilarityReport:
    jaccard: float
    longest_run: int
    longest_run_text: str
    passed: bool
    reason: str = ""

    @property
    def band(self) -> str:
        """Traffic-light banding for the UI."""
        if not self.passed:
            return "rejected"
        if self.jaccard < settings.similarity_jaccard_max * 0.6:
            return "green"
        return "amber"


def _words(text: str) -> list[str]:
    return WORD_RE.findall(text.lower())


def _trigrams(words: list[str]) -> set[tuple[str, str, str]]:
    return {tuple(words[i:i + 3]) for i in range(len(words) - 2)}


def jaccard_similarity(a: str, b: str) -> float:
    """Overlap of word trigrams, 0.0 (disjoint) to 1.0 (identical)."""
    ta, tb = _trigrams(_words(a)), _trigrams(_words(b))
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def longest_common_word_run(a: str, b: str) -> tuple[int, str]:
    """Length and text of the longest verbatim word sequence shared by a and b.

    Classic DP over word sequences. Inputs are single social posts, so the
    quadratic table is a few hundred cells — not worth optimising.
    """
    wa, wb = _words(a), _words(b)
    if not wa or not wb:
        return 0, ""

    previous = [0] * (len(wb) + 1)
    best_len = 0
    best_end_a = 0

    for i in range(1, len(wa) + 1):
        current = [0] * (len(wb) + 1)
        for j in range(1, len(wb) + 1):
            if wa[i - 1] == wb[j - 1]:
                current[j] = previous[j - 1] + 1
                if current[j] > best_len:
                    best_len = current[j]
                    best_end_a = i
        previous = current

    return best_len, " ".join(wa[best_end_a - best_len:best_end_a])


def check_similarity(generated: str, source: str) -> SimilarityReport:
    """Score a draft against the exemplar it was structurally cloned from."""
    jaccard = jaccard_similarity(generated, source)
    run_len, run_text = longest_common_word_run(generated, source)

    reasons = []
    if jaccard > settings.similarity_jaccard_max:
        reasons.append(
            f"trigram overlap {jaccard:.2f} exceeds {settings.similarity_jaccard_max}"
        )
    if run_len >= settings.similarity_max_word_run:
        reasons.append(
            f"shares a {run_len}-word verbatim run (limit {settings.similarity_max_word_run}): "
            f'"{run_text}"'
        )

    return SimilarityReport(
        jaccard=round(jaccard, 4),
        longest_run=run_len,
        longest_run_text=run_text,
        passed=not reasons,
        reason="; ".join(reasons),
    )


def check_hashtag_overlap(generated_tags: list[str], source_tags: list[str]) -> list[str]:
    """Return source tags reproduced verbatim, ignoring unavoidable generics.

    Tags like #AI or #Python are the actual name of the topic — there is no
    paraphrase, and avoiding them would make posts less discoverable, not more
    original. Only distinctive coined tags count as copying.
    """
    generic = {
        "#ai", "#python", "#tech", "#technology", "#startup", "#startups",
        "#software", "#engineering", "#data", "#cloud", "#devops", "#security",
        "#leadership", "#career", "#hiring", "#linkedin", "#marketing",
    }
    gen = {t.lower() for t in generated_tags}
    return [t for t in source_tags if t.lower() in gen and t.lower() not in generic]
