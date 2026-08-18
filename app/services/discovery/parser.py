"""
app/services/discovery/parser.py

Extracts post content from a fetched LinkedIn page.

LinkedIn embeds structured data in public post pages before the authwall is
applied, which is why this works without a login. There are several places that
data can be, and they are not equally rich, so the parser walks them in order
and reports how far it got:

  1. JSON-LD (<script type="application/ld+json">) — fullest: body, author, date
  2. Open Graph meta tags — author and a usually-truncated body, plus og:image
  3. Nothing usable — the caller keeps the SERP snippet and the link

Layer 1 needs raw HTML. A markdown egress (see egress/base.py) has already
stripped <script> tags, so that layer is skipped rather than attempted and
reported as a failure — the distinction matters when diagnosing empty results.

Engagement counts come from the markup rather than from any of those layers; see
_extract_counts for where they live and why the JSON-LD figures are not used.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime

from bs4 import BeautifulSoup

from app.core.logger import get_logger
from app.services.discovery.egress.base import FetchResult

log = get_logger(tag="discovery")

HASHTAG_RE = re.compile(r"#\w+")

AUTHWALL_MARKERS = (
    "authwall",
    "join now to see",
    "sign in to view",
    "please log in",
)


@dataclass
class ParsedPost:
    content_text: str | None = None
    author_name: str | None = None
    author_headline: str | None = None
    author_profile_url: str | None = None
    image_url: str | None = None
    posted_at: datetime | None = None

    # None means "not readable", which must stay distinguishable from 0.
    reactions: int | None = None
    comments: int | None = None
    reposts: int | None = None

    hashtags: list[str] = field(default_factory=list)
    layer: str = "none"          # jsonld | opengraph | markdown | none
    hit_authwall: bool = False

    @property
    def has_content(self) -> bool:
        return bool(self.content_text and self.content_text.strip())

    @property
    def metrics_source(self) -> str:
        # Any readable count makes this partly measured. Keying off `reactions`
        # alone reported real data as inferred — observed on a live post where
        # the comment count parsed but the reaction count did not.
        from app.services.discovery.ranking import describe_basis

        return describe_basis(self.reactions, self.comments, self.reposts)


def _clean(text: str | None) -> str | None:
    if not text:
        return None
    collapsed = re.sub(r"[ \t]+", " ", text)
    collapsed = re.sub(r"\n{3,}", "\n\n", collapsed)
    return collapsed.strip() or None


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    # Stored naive-UTC, consistent with the rest of the schema.
    return parsed.replace(tzinfo=None) if parsed.tzinfo is None else parsed.astimezone().replace(tzinfo=None)


def _post_card(soup: BeautifulSoup):
    """The <article> for the post itself.

    The same card markup repeats for every entry in the "related posts" rail at
    the foot of the page — up to eleven copies observed on one page — so taking
    the first match in the document reads a stranger's numbers. Excluding the
    rail explicitly says which card is meant, rather than relying on the post's
    own card happening to come first.
    """
    for card in soup.find_all("article", class_="main-feed-activity-card"):
        if card.find_parent("section", class_="related-posts") is None:
            return card
    return None


def _anchor_count(card, test_id: str, attribute: str) -> int | None:
    anchor = card.find("a", attrs={"data-test-id": test_id})
    if anchor is None:
        return None
    raw = anchor.get(attribute)
    return int(raw) if raw and raw.isdigit() else None


def _extract_counts(soup: BeautifulSoup) -> dict[str, int | None]:
    """Engagement counts, read from the social-actions anchors.

    LinkedIn server-renders the post's own counts into the initial HTML as
    data-num-* attributes. They are not hydrated client-side: a headless browser
    was measured returning byte-identical numbers on every page a plain fetch
    could read, and rescuing none of the pages it could not (spike S1). There is
    no rendered tier for this and no reason to build one.

    The JSON-LD figures are deliberately not used. `commentCount` there reports
    the length of the inlined `comment[]` array rather than the real total — it
    read 0 on six of thirty-two sampled posts, including one showing 26 comments
    on the page — and the `LikeAction` counter reads 0 on posts with four-figure
    reaction counts. A wrong number is worse than no number here, because
    describe_basis() would then label the row "measured".

    Anchored on data-test-id rather than class names: the classes are
    utility-CSS soup that turns over with every redesign, while these ids held
    across every page sampled.

    An absent anchor stays None. On a post with no engagement LinkedIn omits the
    block entirely, so absence is *probably* a genuine zero — but that is an
    inference about missing markup, and the ranking rule is that a count we did
    not read is None, never 0.
    """
    counts: dict[str, int | None] = {"reactions": None, "comments": None, "reposts": None}

    card = _post_card(soup)
    if card is None:
        return counts

    counts["reactions"] = _anchor_count(
        card, "social-actions__reactions", "data-num-reactions"
    )
    counts["comments"] = _anchor_count(
        card, "social-actions__comments", "data-num-comments"
    )
    # Reposts stay None by construction: no repost or share count is published
    # on a public post page in any form (0 of 35 sampled, plain and rendered).
    return counts


def _from_jsonld(soup: BeautifulSoup, post: ParsedPost) -> bool:
    for tag in soup.find_all("script", type="application/ld+json"):
        raw = tag.string or tag.get_text() or ""
        if not raw.strip():
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue

        candidates = data if isinstance(data, list) else [data]
        # @graph wrappers are common and hold the interesting node one level in.
        for candidate in list(candidates):
            if isinstance(candidate, dict) and "@graph" in candidate:
                graph = candidate["@graph"]
                if isinstance(graph, list):
                    candidates.extend(graph)

        for node in candidates:
            if not isinstance(node, dict):
                continue

            body = node.get("articleBody") or node.get("text") or node.get("description")
            if not body:
                continue

            post.content_text = _clean(body)
            post.posted_at = _parse_iso(node.get("datePublished") or node.get("dateCreated"))

            author = node.get("author")
            if isinstance(author, list) and author:
                author = author[0]
            if isinstance(author, dict):
                post.author_name = _clean(author.get("name"))
                post.author_profile_url = author.get("url")
                post.author_headline = _clean(author.get("jobTitle") or author.get("description"))
            elif isinstance(author, str):
                post.author_name = _clean(author)

            image = node.get("image")
            if isinstance(image, dict):
                post.image_url = image.get("url") or image.get("contentUrl")
            elif isinstance(image, list) and image:
                first = image[0]
                post.image_url = first.get("url") if isinstance(first, dict) else first
            elif isinstance(image, str):
                post.image_url = image

            post.layer = "jsonld"
            return True
    return False


def _from_opengraph(soup: BeautifulSoup, post: ParsedPost) -> bool:
    def meta(prop: str) -> str | None:
        tag = soup.find("meta", property=prop) or soup.find("meta", attrs={"name": prop})
        return tag.get("content") if tag else None

    description = meta("og:description") or meta("twitter:description")
    if not description:
        return False

    post.content_text = _clean(description)
    post.image_url = post.image_url or meta("og:image")

    # og:title on a post page is "Author Name on LinkedIn: <excerpt>".
    title = meta("og:title") or ""
    if " on LinkedIn" in title:
        post.author_name = _clean(title.split(" on LinkedIn")[0])
    elif title:
        post.author_name = _clean(title)

    post.layer = "opengraph"
    return True


def _from_markdown(content: str, post: ParsedPost) -> bool:
    """Salvage what a reader-proxy rendering leaves behind.

    Markdown output has no metadata structure, so this is best-effort: take the
    longest paragraph as the body and hope the title line names the author.
    """
    paragraphs = [p.strip() for p in content.split("\n\n") if p.strip()]
    if not paragraphs:
        return False

    for line in paragraphs[:5]:
        if " on LinkedIn" in line:
            post.author_name = _clean(line.split(" on LinkedIn")[0].lstrip("# ").strip())
            break

    body = max(paragraphs, key=len)
    if len(body) < 40:
        return False

    post.content_text = _clean(body)
    post.layer = "markdown"
    return True


def parse_post(result: FetchResult) -> ParsedPost:
    """Turn a fetched page into whatever post data it actually contains."""
    post = ParsedPost()
    content = result.content or ""

    lowered = content[:4000].lower()
    if any(marker in lowered for marker in AUTHWALL_MARKERS):
        post.hit_authwall = True

    if result.content_kind == "markdown":
        # Layers 1 and 3 need <script> tags, which markdown rendering removed.
        _from_markdown(content, post)
    else:
        soup = BeautifulSoup(content, "html.parser")
        if not _from_jsonld(soup, post):
            _from_opengraph(soup, post)

        counts = _extract_counts(soup)
        post.reactions = counts["reactions"]
        post.comments = counts["comments"]
        post.reposts = counts["reposts"]

    if post.content_text:
        post.hashtags = HASHTAG_RE.findall(post.content_text)

    if not post.has_content:
        log.warning(
            "No post content extracted",
            url=result.url,
            kind=result.content_kind,
            authwall=post.hit_authwall,
        )

    return post
