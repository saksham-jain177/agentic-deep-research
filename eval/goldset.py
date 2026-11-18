"""Gold-set loading and validation.

The gold set is JSON so it can be edited without touching code and diffed in
review:

.. code-block:: json

    {
      "version": 1,
      "queries": [
        {
          "id": "T1-01",
          "tier": "T1",
          "query": "What is the capital of Australia?",
          "expected_claims": [
            {"text": "Canberra is the capital of Australia", "aliases": ["Australia's capital is Canberra"]}
          ],
          "acceptable_domains": ["britannica.com", "gov.au"]
        }
      ]
    }
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Sequence

from .schema import GoldClaim, GoldQuery, TIERS

REQUIRED_TIERS = ("T1", "T2", "T3", "T4")


def load_goldset(path: str) -> List[GoldQuery]:
    with open(path, "r", encoding="utf-8") as handle:
        payload = json.load(handle)
    return parse_goldset(payload)


def parse_goldset(payload: Dict[str, Any]) -> List[GoldQuery]:
    if not isinstance(payload, dict) or "queries" not in payload:
        raise ValueError("gold set must be an object with a 'queries' list")
    queries: List[GoldQuery] = []
    for raw in payload["queries"]:
        claims = [
            GoldClaim(text=str(c["text"]), aliases=tuple(c.get("aliases", ())))
            if isinstance(c, dict)
            else GoldClaim(text=str(c))
            for c in raw.get("expected_claims", [])
        ]
        queries.append(
            GoldQuery(
                id=str(raw["id"]),
                tier=str(raw["tier"]).upper(),
                query=str(raw["query"]),
                expected_claims=tuple(claims),
                acceptable_domains=tuple(raw.get("acceptable_domains", ())),
                must_abstain=bool(raw.get("must_abstain", False)),
            )
        )
    return queries


def validate_goldset(queries: Sequence[GoldQuery], min_per_tier: int = 1) -> List[str]:
    """Return a list of problems; empty means the set is usable."""
    problems: List[str] = []
    seen = set()
    counts = {tier: 0 for tier in TIERS}

    for query in queries:
        if query.id in seen:
            problems.append(f"duplicate id: {query.id}")
        seen.add(query.id)
        counts[query.tier] = counts.get(query.tier, 0) + 1
        if query.tier != "T4" and not query.expected_claims:
            problems.append(f"{query.id}: no expected_claims (only T4 may omit them)")
        if query.tier == "T4" and query.expected_claims:
            problems.append(f"{query.id}: T4 is unanswerable, expected_claims should be empty")
        if len(query.query.split()) < 3:
            problems.append(f"{query.id}: query is too short to be a research question")

    for tier in REQUIRED_TIERS:
        if counts.get(tier, 0) < min_per_tier:
            problems.append(f"tier {tier} has {counts.get(tier, 0)} queries (minimum {min_per_tier})")

    return problems


def tier_counts(queries: Sequence[GoldQuery]) -> Dict[str, int]:
    counts = {tier: 0 for tier in TIERS}
    for query in queries:
        counts[query.tier] = counts.get(query.tier, 0) + 1
    return counts
