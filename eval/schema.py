"""Data model for the evaluation harness."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

TIERS = ("T1", "T2", "T3", "T4")

TIER_DESCRIPTIONS = {
    "T1": "Verifiable fact - one authoritative answer exists",
    "T2": "Synthesis - requires several sources and aggregation",
    "T3": "Contested - sources genuinely disagree",
    "T4": "Unanswerable - no reliable public answer exists (tests abstention)",
}


@dataclass(frozen=True)
class GoldClaim:
    """A claim the report is expected to contain, plus acceptable phrasings."""

    text: str
    aliases: Sequence[str] = ()

    def variants(self) -> List[str]:
        return [self.text, *self.aliases]


@dataclass(frozen=True)
class GoldQuery:
    """One benchmark item."""

    id: str
    tier: str
    query: str
    expected_claims: Sequence[GoldClaim] = ()
    acceptable_domains: Sequence[str] = ()
    must_abstain: bool = False

    def __post_init__(self) -> None:
        if self.tier not in TIERS:
            raise ValueError(f"{self.id}: unknown tier {self.tier!r} (expected one of {TIERS})")
        if self.tier == "T4" and not self.must_abstain:
            raise ValueError(f"{self.id}: T4 queries must set must_abstain=True")
        if self.tier != "T4" and self.must_abstain:
            raise ValueError(f"{self.id}: must_abstain is only valid on T4")


@dataclass
class Reference:
    """A numbered source in the report's reference list."""

    number: int
    raw: str
    url: str = ""
    title: str = ""

    @property
    def domain(self) -> str:
        return extract_domain(self.url)


@dataclass
class Claim:
    """A sentence-level claim lifted from the report body."""

    index: int
    text: str
    citations: List[int] = field(default_factory=list)
    section: str = ""
    # True when the citations were inherited from a nearby sentence
    # (backward attribution: a trailing [n] covers the preceding sentences).
    inherited: bool = False


@dataclass
class PairVerdict:
    """One (claim, citation) evaluation, across the three verification tiers."""

    claim_index: int
    citation_number: int
    link_ok: bool = False
    relevant: Optional[bool] = None
    supported: Optional[bool] = None
    evidence_quote: str = ""
    notes: str = ""

    @property
    def counted(self) -> bool:
        """Whether this pair enters the citation-accuracy denominator."""
        return self.link_ok and self.relevant is not None

    @property
    def is_supported(self) -> bool:
        return bool(self.link_ok and self.relevant and self.supported)


@dataclass
class PipelineOutput:
    """What the agent produced for one query."""

    report: str
    sources: List[Dict[str, Any]] = field(default_factory=list)
    latency_s: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    error: str = ""


@dataclass
class QueryResult:
    """Evaluation outcome for one gold query."""

    query_id: str
    tier: str
    query: str
    pairs: List[PairVerdict] = field(default_factory=list)
    claims: List[Claim] = field(default_factory=list)
    covered_claims: int = 0
    expected_claims: int = 0
    abstained: Optional[bool] = None
    latency_s: float = 0.0
    cost_usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    n_sources: int = 0
    error: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "query_id": self.query_id,
            "tier": self.tier,
            "query": self.query,
            "covered_claims": self.covered_claims,
            "expected_claims": self.expected_claims,
            "abstained": self.abstained,
            "latency_s": round(self.latency_s, 3),
            "cost_usd": round(self.cost_usd, 6),
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "n_sources": self.n_sources,
            "n_claims": len(self.claims),
            "n_pairs": len(self.pairs),
            "error": self.error,
            "pairs": [p.__dict__ for p in self.pairs],
        }


_URL_RE = re.compile(r"https?://[^\s\)\]\>\"']+")


def extract_domain(url: str) -> str:
    """Lowercased registrable-ish domain: strips scheme, www, path, port."""
    if not url:
        return ""
    m = _URL_RE.search(url)
    if not m:
        return ""
    host = m.group(0).split("//", 1)[-1]
    host = host.split("/", 1)[0].split("?", 1)[0].split("#", 1)[0]
    host = host.rsplit("@", 1)[-1].split(":", 1)[0].lower()
    return host[4:] if host.startswith("www.") else host


def extract_url(text: str) -> str:
    """First URL in a string, or empty string."""
    m = _URL_RE.search(text or "")
    return m.group(0).rstrip(".,;") if m else ""
