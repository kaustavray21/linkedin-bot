"""
app/services/layout_service.py

Captures the *shape* of a single post — how it breaks into blocks and lines —
and reproduces that shape in generated output.

Why this exists: style_service.extract_style_profile() reduces a set of posts to
averages (avg_word_count, avg_line_count). Averaging is lossy in exactly the
place that matters. A creator who deliberately leaves a two-word line alone on
its own row, then follows it with a dense five-line block, has an average that
describes neither. Feeding that average to a model produces uniform mid-length
paragraphs — the "AI-generated" texture we are trying to eliminate.

So this module never averages. It reads ONE exemplar and records its actual
per-line, per-block structure, which is then (a) rendered into the prompt as an
explicit template and (b) enforced on the model's output deterministically.

Nothing here stores or returns the exemplar's wording — a LayoutSkeleton is pure
structure, which is why it is safe to retain after the source text is purged.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

HASHTAG_RE = re.compile(r"#\w+")
EMOJI_RE = re.compile(
    "[\U0001F300-\U0001FAFF\U00002700-\U000027BF\U0001F900-\U0001F9FF]"
)
LIST_MARKER_RE = re.compile(r"^\s*(?:[-*•·▪️→›]|\d+[.)]|[✅✔️❌🔹🔸])\s+")
SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")

TERMINALS = (".", "?", "!", ":", "…", "—", "→", ",", ";")


@dataclass
class LineSpec:
    words: int
    chars: int
    ends_with: str
    has_emoji: bool
    is_hashtag_line: bool
    is_list_item: bool


@dataclass
class BlockSpec:
    lines: list[LineSpec] = field(default_factory=list)
    blank_after: int = 1

    @property
    def is_hashtag_block(self) -> bool:
        return bool(self.lines) and all(line.is_hashtag_line for line in self.lines)

    @property
    def word_total(self) -> int:
        return sum(line.words for line in self.lines)


@dataclass
class LayoutSkeleton:
    blocks: list[BlockSpec] = field(default_factory=list)
    hook_lines: int = 0
    hashtag_placement: str = "none"      # trailing_block | inline | none
    hashtag_count: int = 0
    emoji_positions: list[int] = field(default_factory=list)
    total_words: int = 0

    @property
    def total_blocks(self) -> int:
        return len(self.blocks)

    @property
    def content_blocks(self) -> list[BlockSpec]:
        """Blocks carrying prose — the hashtag block is handled separately."""
        return [b for b in self.blocks if not b.is_hashtag_block]

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> LayoutSkeleton:
        blocks = [
            BlockSpec(
                lines=[LineSpec(**line) for line in block.get("lines", [])],
                blank_after=block.get("blank_after", 1),
            )
            for block in data.get("blocks", [])
        ]
        return cls(
            blocks=blocks,
            hook_lines=data.get("hook_lines", 0),
            hashtag_placement=data.get("hashtag_placement", "none"),
            hashtag_count=data.get("hashtag_count", 0),
            emoji_positions=data.get("emoji_positions", []),
            total_words=data.get("total_words", 0),
        )


def _classify_line(raw: str) -> LineSpec:
    line = raw.strip()
    words = line.split()
    tags = HASHTAG_RE.findall(line)
    # A hashtag line is one that is *only* hashtags — a line ending in a single
    # trailing tag is still prose and must not be treated as the hashtag block.
    is_hashtag_line = bool(tags) and len(tags) == len(words)
    ends_with = line[-1] if line and line[-1] in TERMINALS else ""
    return LineSpec(
        words=len(words),
        chars=len(line),
        ends_with=ends_with,
        has_emoji=bool(EMOJI_RE.search(line)),
        is_hashtag_line=is_hashtag_line,
        is_list_item=bool(LIST_MARKER_RE.match(raw)),
    )


def _split_blocks(text: str) -> list[tuple[list[str], int]]:
    """Split into (lines, blank_after) pairs, preserving how wide each gap was.

    A creator who separates blocks with two blank lines instead of one is making
    a deliberate rhythmic choice; collapsing that to a boolean loses it.
    """
    lines = text.replace("\r\n", "\n").split("\n")
    blocks: list[tuple[list[str], int]] = []
    current: list[str] = []
    blanks = 0

    for line in lines:
        if line.strip():
            if blanks and current:
                blocks.append((current, min(blanks, 2)))
                current = []
            blanks = 0
            current.append(line)
        else:
            if current:
                blanks += 1

    if current:
        blocks.append((current, 1))
    return blocks


def extract_skeleton(text: str) -> LayoutSkeleton:
    """Read one exemplar post and record its structure."""
    if not text or not text.strip():
        raise ValueError("Cannot extract a layout skeleton from empty text")

    raw_blocks = _split_blocks(text)
    blocks = [
        BlockSpec(lines=[_classify_line(line) for line in lines], blank_after=blank)
        for lines, blank in raw_blocks
    ]

    all_tags = HASHTAG_RE.findall(text)
    if not all_tags:
        placement = "none"
    elif blocks and blocks[-1].is_hashtag_block:
        placement = "trailing_block"
    else:
        placement = "inline"

    emoji_positions = [
        idx for idx, block in enumerate(blocks)
        if any(line.has_emoji for line in block.lines)
    ]

    return LayoutSkeleton(
        blocks=blocks,
        hook_lines=len(blocks[0].lines) if blocks else 0,
        hashtag_placement=placement,
        hashtag_count=len(all_tags),
        emoji_positions=emoji_positions,
        total_words=sum(b.word_total for b in blocks),
    )


def _merge_blocks(first: BlockSpec, second: BlockSpec) -> BlockSpec:
    """Join two adjacent blocks, keeping the gap that followed the second."""
    return BlockSpec(lines=[*first.lines, *second.lines], blank_after=second.blank_after)


def _halve_line(line: LineSpec) -> tuple[LineSpec, LineSpec]:
    """Divide one line's budget in two, for when every block is a single line.

    The tail keeps the original ending and any emoji, because those describe how
    the thought finishes. The head gets no `ends_with`: we are inventing a break
    that the exemplar did not have, and render_template omits the clause entirely
    rather than inventing punctuation for it.
    """
    head_words = max(1, line.words // 2)
    tail_words = max(1, line.words - head_words)
    ratio = head_words / max(line.words, 1)

    head = LineSpec(
        words=head_words,
        chars=max(1, int(line.chars * ratio)),
        ends_with="",
        has_emoji=False,
        is_list_item=line.is_list_item,
        is_hashtag_line=False,
    )
    tail = LineSpec(
        words=tail_words,
        chars=max(1, line.chars - head.chars),
        ends_with=line.ends_with,
        has_emoji=line.has_emoji,
        is_list_item=line.is_list_item,
        is_hashtag_line=False,
    )
    return head, tail


def _split_block(block: BlockSpec) -> tuple[BlockSpec, BlockSpec]:
    """Divide one block in two, preferring an existing line boundary."""
    if len(block.lines) >= 2:
        middle = len(block.lines) // 2
        return (
            BlockSpec(lines=block.lines[:middle], blank_after=1),
            BlockSpec(lines=block.lines[middle:], blank_after=block.blank_after),
        )

    head, tail = _halve_line(block.lines[0])
    return BlockSpec(lines=[head], blank_after=1), BlockSpec(lines=[tail], blank_after=block.blank_after)


def retarget_skeleton(skeleton: LayoutSkeleton, target_blocks: int) -> LayoutSkeleton:
    """Reshape a skeleton to `target_blocks` content blocks.

    The paragraph control has to be structural. Asking the model in prose for
    "3 paragraphs" is the same class of instruction this module exists to
    replace — it drifts, and enforce_layout would then drag the output back to
    the *exemplar's* block count and undo the request. Retargeting the skeleton
    first means the template, the prompt and the enforcement all agree on the
    number the user asked for.

    Merging picks the shortest adjacent pair and splitting divides the longest
    block, so the reshaping stays as close to the original rhythm as the new
    count allows. The trailing hashtag block is never counted or touched.

    Returns the skeleton unchanged when the target already matches, and when the
    exemplar has no prose to redistribute — a request to reshape nothing is a
    no-op, not a reason to fail the generation that asked for it.
    """
    if target_blocks < 1:
        raise ValueError("A post needs at least one paragraph")

    content = [b for b in skeleton.blocks if not b.is_hashtag_block]
    trailing = [b for b in skeleton.blocks if b.is_hashtag_block]
    if not content or len(content) == target_blocks:
        return skeleton

    blocks = list(content)

    while len(blocks) > target_blocks:
        shortest = min(
            range(len(blocks) - 1),
            key=lambda i: blocks[i].word_total + blocks[i + 1].word_total,
        )
        blocks[shortest] = _merge_blocks(blocks[shortest], blocks[shortest + 1])
        del blocks[shortest + 1]

    while len(blocks) < target_blocks:
        longest = max(range(len(blocks)), key=lambda i: blocks[i].word_total)
        head, tail = _split_block(blocks[longest])
        blocks[longest:longest + 1] = [head, tail]

    reshaped = blocks + trailing
    return LayoutSkeleton(
        blocks=reshaped,
        hook_lines=len(reshaped[0].lines) if reshaped else 0,
        hashtag_placement=skeleton.hashtag_placement,
        hashtag_count=skeleton.hashtag_count,
        emoji_positions=[
            idx for idx, block in enumerate(reshaped)
            if any(line.has_emoji for line in block.lines)
        ],
        total_words=sum(b.word_total for b in reshaped),
    )


def render_template(skeleton: LayoutSkeleton) -> str:
    """Render the skeleton as an explicit shape for the model to fill.

    Prose instructions like "match the paragraphing of the references" do not
    work when the references are not in the prompt — the model has nothing to
    match against and falls back on its default cadence. A literal per-block
    spec gives it something concrete to satisfy.
    """
    parts: list[str] = []
    for index, block in enumerate(skeleton.blocks, start=1):
        if block.is_hashtag_block:
            parts.append(
                f"Block {index}: hashtags only — exactly {skeleton.hashtag_count} "
                f"tags on {len(block.lines)} line(s), no other words."
            )
            continue

        line_descriptions = []
        for line in block.lines:
            # Approximate, not exact. Demanding an exact count makes the model
            # cut sentences mid-clause to hit the number — observed producing
            # breaks like "The fear of imperfect launch" / "Keeps so many from
            # launching at all." Line *count* is what carries the rhythm; the
            # word count only needs to be in the right neighbourhood.
            desc = f"~{line.words} word{'s' if line.words != 1 else ''}"
            if line.is_list_item:
                desc += ", list item"
            if line.ends_with:
                desc += f', ends "{line.ends_with}"'
            if line.has_emoji:
                desc += ", contains an emoji"
            line_descriptions.append(desc)

        if len(block.lines) == 1:
            parts.append(f"Block {index}: 1 line — {line_descriptions[0]}.")
        else:
            joined = " / ".join(line_descriptions)
            parts.append(f"Block {index}: {len(block.lines)} lines — {joined}.")

        if block.blank_after == 2:
            parts.append("(double blank line here)")

    header = (
        f"Reproduce this shape — {skeleton.total_blocks} blocks separated by blank lines, "
        f"roughly {skeleton.total_words} words total:"
    )
    footer = (
        "\nRules for the shape:\n"
        "- The number of blocks and the number of lines in each block are exact.\n"
        "- Word counts are approximate targets, not quotas.\n"
        "- Every line break must fall at a natural clause or sentence boundary. "
        "If a line will not fit its target, rewrite the sentence shorter — never "
        "cut a sentence partway through to hit a number.\n"
        "- Each line should read as a complete thought."
    )
    return header + "\n" + "\n".join(parts) + "\n" + footer


# --------------------------------------------------------------- enforcement --

def _blocks_of(text: str) -> list[list[str]]:
    return [lines for lines, _ in _split_blocks(text)]


def _split_block_to(lines: list[str], target: int) -> list[list[str]]:
    """Grow one block into `target` blocks by splitting on sentences."""
    sentences: list[str] = []
    for line in lines:
        sentences.extend(s for s in SENTENCE_SPLIT_RE.split(line.strip()) if s)

    if len(sentences) < target:
        return [[line] for line in lines] or [[""]]

    # Distribute sentences as evenly as possible across the target blocks.
    out: list[list[str]] = []
    per, extra = divmod(len(sentences), target)
    cursor = 0
    for i in range(target):
        take = per + (1 if i < extra else 0)
        out.append([" ".join(sentences[cursor:cursor + take])])
        cursor += take
    return out


def enforce_layout(text: str, skeleton: LayoutSkeleton) -> str:
    """Coerce generated text to the skeleton's block structure.

    Models drift on formatting even when the template is explicit, and the drift
    is always toward their default cadence. Correcting it here — deterministically
    — is cheaper and more reliable than another round of prompt pleading.

    Content is never rewritten, only regrouped: excess blocks merge into their
    predecessor, missing blocks are produced by splitting on sentence boundaries.
    """
    if not text.strip() or not skeleton.blocks:
        return text.strip()

    blocks = _blocks_of(text)
    if not blocks:
        return text.strip()

    # Separate a trailing hashtag block so regrouping never disturbs it.
    tail_tags: list[str] | None = None
    if skeleton.hashtag_placement == "trailing_block" and blocks:
        last = blocks[-1]
        spec = [_classify_line(line) for line in last]
        if spec and all(s.is_hashtag_line for s in spec):
            tail_tags = blocks.pop()

    target_blocks = [b for b in skeleton.blocks if not b.is_hashtag_block]
    target = len(target_blocks)

    if blocks and target:
        while len(blocks) > target:
            # Merge the shortest adjacent pair — least disruptive to rhythm.
            shortest = min(
                range(len(blocks) - 1),
                key=lambda i: sum(len(x.split()) for x in blocks[i]),
            )
            blocks[shortest] = blocks[shortest] + blocks[shortest + 1]
            del blocks[shortest + 1]

        if len(blocks) < target:
            # Redistribute every sentence across the target block count in one
            # pass. Splitting the longest block repeatedly stalls as soon as
            # that block is a single sentence, even when other blocks could
            # still yield one.
            flattened = [line for block in blocks for line in block]
            blocks = _split_block_to(flattened, target)

    rendered = ["\n".join(line.strip() for line in block) for block in blocks]

    out = ""
    for index, block_text in enumerate(rendered):
        out += block_text
        is_last = index == len(rendered) - 1
        if not is_last or tail_tags:
            # blank_after counts blank lines, so the separator is that many
            # newlines plus the one that ends the current line.
            gap = target_blocks[index].blank_after if index < len(target_blocks) else 1
            out += "\n" * (gap + 1)

    if tail_tags:
        out += "\n".join(line.strip() for line in tail_tags)

    return out.strip()
