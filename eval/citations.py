"""Parse a generated report into numbered references and sentence-level claims.

The agent's reports are Markdown: headed sections of prose carrying inline
``[n]`` markers, a numbered reference list, and two appended blocks
(``## Verification Summary`` and ``## Conflicting Evidence``) that are
metadata rather than research prose.

This module turns that shape into two structures the metrics can reason
about: a ``{n: Reference}`` registry and a list of ``Claim`` objects whose
``citations`` point into the registry.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .schema import Claim, Reference, extract_url

# Headings whose body is not research prose and must not be scored as claims.
NON_CONTENT_HEADINGS = (
    "references",
    "sources",
    "bibliography",
    "works cited",
    "verification summary",
    "conflicting evidence",
    "cost",
    "appendix",
)

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
_REFERENCE_LINE_RE = re.compile(r"^\s*(?:[-*]\s*)?\[(\d+)\]\s+(.+)$")
_INLINE_CITATION_RE = re.compile(r"\[(\d+(?:\s*[-,]\s*\d+)*)\]")
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])[\"')\]]*\s+(?=[\"'(\[]?[A-Z0-9])")

# Sentences shorter than this are headings, labels, or fragments.
MIN_CLAIM_WORDS = 5
MAX_CLAIM_CHARS = 1200


@dataclass
class ParsedReport:
    references: Dict[int, Reference] = field(default_factory=dict)
    claims: List[Claim] = field(default_factory=list)
    sections: Dict[str, str] = field(default_factory=dict)
    dangling: List[int] = field(default_factory=list)

    @property
    def claim_count(self) -> int:
        return len(self.claims)

    @property
    def cited_claims(self) -> int:
        return sum(1 for c in self.claims if c.citations)


def _expand_citation_numbers(inner: str) -> List[int]:
    """Expand ``1,2`` and ``4-6`` into a sorted, de-duplicated list."""
    numbers: List[int] = []
    for part in re.split(r"[,\s]+", inner.strip()):
        if not part:
            continue
        if "-" in part:
            lo, _, hi = part.partition("-")
            try:
                start, end = int(lo), int(hi)
            except ValueError:
                continue
            if 0 < start <= end and end - start <= 60:
                numbers.extend(range(start, end + 1))
        else:
            try:
                numbers.append(int(part))
            except ValueError:
                continue
    return sorted(set(n for n in numbers if n > 0))


def extract_references(text: str) -> Dict[int, Reference]:
    """Collect ``[n] ...`` reference lines from anywhere in the report."""
    refs: Dict[int, Reference] = {}
    for line in text.splitlines():
        m = _REFERENCE_LINE_RE.match(line)
        if not m:
            continue
        number = int(m.group(1))
        body = m.group(2).strip()
        if not body:
            continue
        url = extract_url(body)
        refs[number] = Reference(
            number=number,
            raw=body,
            url=url,
            title=_guess_title(body, url),
        )
    return refs


def _guess_title(body: str, url: str) -> str:
    """Title heuristic: text before the first period/author-year/URL."""
    head = body.split(url, 1)[0] if url else body
    head = re.split(r"\.\s", head, maxsplit=1)[0]
    head = re.sub(r"\s+", " ", head).strip(" .;,|-")
    return head[:200]


def _split_sections(text: str) -> List[Tuple[str, str]]:
    """Return ``[(heading, body), ...]`` in document order."""
    sections: List[Tuple[str, str]] = []
    heading = ""
    buffer: List[str] = []
    for line in text.splitlines():
        m = _HEADING_RE.match(line)
        if m:
            sections.append((heading, "\n".join(buffer)))
            heading = m.group(2).strip()
            buffer = []
        else:
            buffer.append(line)
    sections.append((heading, "\n".join(buffer)))
    return sections


def _is_non_content(heading: str) -> bool:
    h = heading.lower()
    return any(word in h for word in NON_CONTENT_HEADINGS)


def _clean_sentence(sentence: str) -> str:
    s = sentence.strip()
    s = re.sub(r"^\s*(?:[-*+]|\d+\.)\s+", "", s)  # list markers
    s = re.sub(r"\*\*(.+?)\*\*", r"\1", s)  # bold
    s = re.sub(r"\*(.+?)\*", r"\1", s)  # italic
    s = re.sub(r"[ \t]+", " ", s)
    return s.strip()


def split_claims(
    body: str,
    section: str = "",
    start_index: int = 0,
    backward_attribution: bool = True,
) -> List[Claim]:
    """Split one section body into claims, attaching inline citation numbers.

    With ``backward_attribution`` a trailing citation also covers the
    uncited sentences immediately before it inside the same paragraph, which
    is how the agent cites and how the literature evaluates it.
    """
    claims: List[Claim] = []
    next_index = start_index

    for paragraph in re.split(r"\n\s*\n", body):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        paragraph_claims: List[Claim] = []
        for raw in _SENTENCE_SPLIT_RE.split(paragraph):
            text = _clean_sentence(raw)
            if not text or len(text) > MAX_CLAIM_CHARS:
                continue
            numbers: List[int] = []
            for m in _INLINE_CITATION_RE.finditer(text):
                numbers.extend(_expand_citation_numbers(m.group(1)))
            numbers = sorted(set(numbers))
            literal = _INLINE_CITATION_RE.sub("", text).strip()
            if len(literal.split()) < MIN_CLAIM_WORDS:
                continue
            claim = Claim(index=next_index, text=literal, citations=numbers, section=section)
            next_index += 1
            paragraph_claims.append(claim)

        if backward_attribution:
            # Within a paragraph, a trailing citation also covers the
            # uncited sentences immediately before it.
            for i in range(len(paragraph_claims) - 1, 0, -1):
                current, previous = paragraph_claims[i], paragraph_claims[i - 1]
                if previous.citations or not current.citations:
                    continue
                previous.citations = list(current.citations)
                previous.inherited = True

        claims.extend(paragraph_claims)

    return claims


def parse_report(report: str, backward_attribution: bool = True) -> ParsedReport:
    """Parse a full report into references + claims."""
    if not report or not report.strip():
        return ParsedReport()

    references = extract_references(report)
    parsed = ParsedReport(references=references)
    index = 0

    for heading, body in _split_sections(report):
        if not body.strip():
            continue
        if _is_non_content(heading):
            continue
        parsed.sections[heading or "(untitled)"] = body
        section_claims = split_claims(
            body,
            section=heading or "(untitled)",
            start_index=index,
            backward_attribution=backward_attribution,
        )
        index += len(section_claims)
        parsed.claims.extend(section_claims)

    seen = set()
    for claim in parsed.claims:
        for n in claim.citations:
            if n not in references and n not in seen:
                seen.add(n)
                parsed.dangling.append(n)

    return parsed


def find_reference_text(
    number: int,
    sources: List[dict],
    references: Dict[int, Reference],
) -> str:
    """Recover the body text for citation ``n`` from the gathered sources.

    Matching order: exact URL, then same domain, then the reference number's
    position in the source list (the agent numbers sources in order).
    """
    ref: Optional[Reference] = references.get(number)
    if ref is None:
        return ""
    if ref.url:
        for src in sources:
            if str(src.get("url", "")).strip() == ref.url:
                return str(src.get("content", "") or "")
        ref_domain = ref.domain
        for src in sources:
            src_url = str(src.get("url", ""))
            if ref_domain and ref_domain in src_url:
                return str(src.get("content", "") or "")
    if 0 < number <= len(sources):
        return str(sources[number - 1].get("content", "") or "")
    return ""
