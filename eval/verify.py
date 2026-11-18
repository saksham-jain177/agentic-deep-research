"""Three-tier verification of every (claim, citation) pair.

Tier 1 - ``link_ok``   : the cited URL resolves to retrievable content
Tier 2 - ``relevant``  : that content addresses the claim's subject
Tier 3 - ``supported`` : that content actually establishes the claim

The tiers are cumulative on purpose: a claim cannot be "supported" by a page
that never loaded, or by a page about something else. Reporting them
separately is what makes a failure diagnosable instead of just a low score.
"""

from __future__ import annotations

from typing import Callable, Dict, List, Optional, Sequence

from .citations import ParsedReport, find_reference_text
from .judge import Judge, content_tokens
from .schema import Claim, GoldQuery, PairVerdict

LinkChecker = Callable[[str], bool]

MAX_PAIRS_PER_QUERY = 400


def default_link_checker(url: str, timeout: float = 10.0) -> bool:
    """HTTP HEAD with a GET fallback; any 2xx/3xx counts as reachable."""
    if not url:
        return False
    try:
        import requests

        headers = {"User-Agent": "Mozilla/5.0 (compatible; deep-research-eval/0.1)"}
        response = requests.head(url, timeout=timeout, allow_redirects=True, headers=headers)
        if response.status_code >= 400:
            response = requests.get(url, timeout=timeout, allow_redirects=True, headers=headers, stream=True)
        return response.status_code < 400
    except Exception:
        return False


def verify_pairs(
    parsed: ParsedReport,
    sources: Sequence[dict],
    judge: Judge,
    link_checker: Optional[LinkChecker] = None,
    max_pairs: int = MAX_PAIRS_PER_QUERY,
) -> List[PairVerdict]:
    """Evaluate every (claim, citation) pair in a parsed report.

    ``link_checker`` is optional: without it, tier 1 falls back to "did the
    research step capture content for this citation", which keeps the harness
    runnable offline. Pass ``default_link_checker`` for a live URL check.
    """
    verdicts: List[PairVerdict] = []

    for claim in parsed.claims:
        for number in claim.citations:
            if len(verdicts) >= max_pairs:
                return verdicts
            source_text = find_reference_text(number, list(sources), parsed.references)
            reference = parsed.references.get(number)
            url = reference.url if reference else ""

            if link_checker is not None:
                link_ok = link_checker(url)
            else:
                link_ok = bool(source_text.strip())

            verdict = PairVerdict(
                claim_index=claim.index,
                citation_number=number,
                link_ok=link_ok,
            )

            if not link_ok:
                verdict.notes = "citation did not resolve" if url else "no URL on reference"
                verdicts.append(verdict)
                continue
            if not source_text.strip():
                verdict.notes = "no captured content for this source"
                verdicts.append(verdict)
                continue
            if reference is None:
                verdict.notes = f"dangling citation [{number}]"

            relevant = judge.relevance(claim.text, source_text)
            verdict.relevant = relevant
            if relevant:
                verdict.supported = judge.support(claim.text, source_text)
                if verdict.supported:
                    verdict.evidence_quote = judge.evidence_quote(claim.text, source_text)

            verdicts.append(verdict)

    return verdicts


def _gold_tokens(text: str) -> set:
    return set(content_tokens(text))


def claim_matches(gold_text: str, claim_text: str, threshold: float = 0.7) -> bool:
    """Does ``claim_text`` express ``gold_text``?

    Containment of the gold claim's content words, so a longer sentence that
    states the same fact still counts. Any number in the gold claim must
    appear in the candidate - "37%" is not a match for "73%".
    """
    from .judge import _numbers

    gold = _gold_tokens(gold_text)
    if not gold:
        return False
    candidate = _gold_tokens(claim_text)
    if len(gold & candidate) / len(gold) < threshold:
        return False
    required_numbers = _numbers(gold_text)
    return required_numbers.issubset(_numbers(claim_text)) if required_numbers else True


def count_covered_claims(parsed: ParsedReport, gold: GoldQuery) -> int:
    """How many of the gold query's expected claims appear in the report."""
    covered = 0
    claim_texts = [c.text for c in parsed.claims]
    for expected in gold.expected_claims:
        if any(claim_matches(variant, text) for variant in expected.variants() for text in claim_texts):
            covered += 1
    return covered


def summarise_pairs(verdicts: Sequence[PairVerdict]) -> Dict[str, int]:
    return {
        "pairs": len(verdicts),
        "link_ok": sum(1 for v in verdicts if v.link_ok),
        "relevant": sum(1 for v in verdicts if v.relevant),
        "supported": sum(1 for v in verdicts if v.is_supported),
        "unknown": sum(1 for v in verdicts if v.relevant is None),
    }


def claims_without_citations(claims: Sequence[Claim]) -> List[Claim]:
    return [c for c in claims if not c.citations]
