"""Judges for the three citation-verification tiers.

A judge answers two binary questions per (claim, source) pair:

* ``relevance`` - does the source address the same topic as the claim?
* ``support``   - does the source actually back the specific claim?

Two implementations ship here:

``HeuristicJudge``
    Deterministic lexical overlap. No network, no keys, runs in CI. It is a
    *conservative lower bound*: it will miss paraphrase support, so its
    support rate understates the agent. Use it to keep the harness runnable,
    never to publish a headline number.

``LLMJudge``
    Wraps any ``complete(prompt) -> str`` callable, so the transport is
    pluggable and testable. Forces a JSON verdict with a required evidence
    quote, because free-form scores are not reproducible.

Judge quality is itself measured: see ``eval.metrics.cohen_kappa`` and the
``calibrate`` CLI command.
"""

from __future__ import annotations

import json
import math
import os
import re
from dataclasses import dataclass
from typing import Callable, List, Optional, Protocol, Sequence, Tuple

STOPWORDS = {
    "a", "about", "after", "all", "also", "an", "and", "any", "are", "as", "at",
    "be", "because", "been", "before", "being", "between", "both", "but", "by",
    "can", "could", "did", "do", "does", "doing", "during", "each", "few", "for",
    "from", "further", "had", "has", "have", "having", "he", "her", "here", "hers",
    "him", "his", "how", "i", "if", "in", "into", "is", "it", "its", "itself",
    "just", "me", "more", "most", "my", "no", "nor", "not", "now", "of", "off",
    "on", "once", "only", "or", "other", "our", "out", "over", "own", "same",
    "she", "should", "so", "some", "such", "than", "that", "the", "their", "them",
    "then", "there", "these", "they", "this", "those", "through", "to", "too",
    "under", "until", "up", "very", "was", "we", "were", "what", "when", "where",
    "which", "while", "who", "whom", "why", "will", "with", "would", "you", "your",
}

_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9'\-]*")
_NUMBER_RE = re.compile(r"\b\d[\d,.]*\s*(?:%|percent|million|billion|trillion|bn|k)?\b")


class Judge(Protocol):
    name: str

    def relevance(self, claim: str, source: str) -> Optional[bool]:
        ...

    def support(self, claim: str, source: str) -> Optional[bool]:
        ...

    def evidence_quote(self, claim: str, source: str) -> str:
        ...


def content_tokens(text: str) -> List[str]:
    return [t for t in _TOKEN_RE.findall((text or "").lower()) if t not in STOPWORDS and len(t) > 1]


def _jaccard(a: Sequence[str], b: Sequence[str]) -> float:
    sa, sb = set(a), set(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def _numbers(text: str) -> set:
    return {n.replace(",", "").strip() for n in _NUMBER_RE.findall(text or "")}


@dataclass
class HeuristicJudge:
    """Deterministic lexical judge - conservative and offline."""

    name: str = "heuristic"
    relevance_threshold: float = 0.06
    support_threshold: float = 0.55
    max_source_chars: int = 8000

    def _tokens(self, claim: str, source: str) -> Tuple[List[str], List[str]]:
        return content_tokens(claim), content_tokens(source[: self.max_source_chars])

    def relevance(self, claim: str, source: str) -> Optional[bool]:
        if not (claim or "").strip() or not (source or "").strip():
            return None
        claim_tokens, source_tokens = self._tokens(claim, source)
        return _jaccard(claim_tokens, source_tokens) >= self.relevance_threshold

    def support(self, claim: str, source: str) -> Optional[bool]:
        if not (claim or "").strip() or not (source or "").strip():
            return None
        claim_tokens, source_tokens = self._tokens(claim, source)
        if not claim_tokens:
            return None
        # Directional coverage, not symmetric Jaccard: how much of the claim
        # is actually present in the source.
        coverage = len(set(claim_tokens) & set(source_tokens)) / len(set(claim_tokens))
        if coverage < self.support_threshold:
            return False
        # Numbers are the highest-risk tokens in research prose: a claim
        # citing "37%" is not supported by a source saying "73%".
        claim_numbers = _numbers(claim)
        if claim_numbers:
            return claim_numbers.issubset(_numbers(source))
        return True

    def evidence_quote(self, claim: str, source: str) -> str:
        if not (claim or "").strip() or not (source or "").strip():
            return ""
        claim_tokens = set(content_tokens(claim))
        best, best_score = "", 0.0
        for sentence in re.split(r"(?<=[.!?])\s+", source[: self.max_source_chars]):
            tokens = set(content_tokens(sentence))
            if not tokens:
                continue
            score = len(tokens & claim_tokens) / math.sqrt(len(claim_tokens) or 1)
            if score > best_score:
                best, best_score = sentence.strip(), score
        return best[:400]


RELEVANCE_PROMPT = """You are evaluating whether a source is on-topic for a claim.

CLAIM:
{claim}

SOURCE EXCERPT:
{source}

Answer only whether the source addresses the same subject matter as the claim.
Being on-topic is NOT the same as supporting the claim.

Respond with JSON only:
{{"relevant": true|false, "reason": "<one sentence>"}}"""

SUPPORT_PROMPT = """You are a strict fact-verification judge.

CLAIM:
{claim}

SOURCE EXCERPT:
{source}

Decide whether the source excerpt itself establishes the claim. Use only the
excerpt; never outside knowledge. A source that merely mentions the topic does
not support a specific number, date, or attribution.

Respond with JSON only:
{{"supported": true|false, "quote": "<verbatim span from the excerpt that establishes the claim, or an empty string>", "reason": "<one sentence>"}}"""


def _extract_json(text: str) -> dict:
    cleaned = re.sub(r"<think>.*?</think>", "", text or "", flags=re.DOTALL).strip()
    cleaned = re.sub(r"^```(?:json)?|```$", "", cleaned, flags=re.MULTILINE).strip()
    m = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
    if not m:
        raise ValueError("no JSON object in judge response")
    obj = json.loads(m.group(0))
    if not isinstance(obj, dict):
        raise ValueError("judge response is not a JSON object")
    return obj


def _as_bool(value) -> Optional[bool]:
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().lower() in {"true", "false", "yes", "no"}:
        return value.strip().lower() in {"true", "yes"}
    return None


@dataclass
class LLMJudge:
    """LLM-backed judge over an injectable completion callable.

    ``complete`` receives a prompt and returns the model's raw text. Injecting
    it keeps this class free of any SDK dependency and lets tests assert on
    prompt construction and JSON parsing without a network call.
    """

    complete: Callable[[str], str]
    name: str = "llm"
    max_source_chars: int = 6000
    _cache: dict = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self._cache is None:
            self._cache = {}

    def _clip(self, source: str) -> str:
        return (source or "")[: self.max_source_chars]

    def _ask(self, prompt: str) -> dict:
        key = str(hash(prompt))
        if key in self._cache:
            return self._cache[key]
        obj = _extract_json(self.complete(prompt))
        self._cache[key] = obj
        return obj

    def relevance(self, claim: str, source: str) -> Optional[bool]:
        if not (claim or "").strip() or not (source or "").strip():
            return None
        try:
            obj = self._ask(RELEVANCE_PROMPT.format(claim=claim, source=self._clip(source)))
        except Exception:
            return None
        return _as_bool(obj.get("relevant"))

    def support(self, claim: str, source: str) -> Optional[bool]:
        if not (claim or "").strip() or not (source or "").strip():
            return None
        try:
            obj = self._ask(SUPPORT_PROMPT.format(claim=claim, source=self._clip(source)))
        except Exception:
            return None
        return _as_bool(obj.get("supported"))

    def evidence_quote(self, claim: str, source: str) -> str:
        if not (claim or "").strip() or not (source or "").strip():
            return ""
        try:
            obj = self._ask(SUPPORT_PROMPT.format(claim=claim, source=self._clip(source)))
        except Exception:
            return ""
        return str(obj.get("quote", ""))[:400]


def build_openrouter_judge(model: Optional[str] = None) -> Optional[Judge]:
    """Construct an ``LLMJudge`` from the same env vars the agent uses.

    Returns ``None`` when no API key is configured, so callers can fall back
    to the heuristic judge instead of crashing.
    """
    api_key = os.getenv("EVAL_JUDGE_API_KEY") or os.getenv("OPENROUTER_API_KEY") or os.getenv("OPENAI_API_KEY")
    if not api_key:
        return None

    base_url = os.getenv("OPENAI_BASE_URL") or "https://openrouter.ai/api/v1"
    model = model or os.getenv("EVAL_JUDGE_MODEL") or os.getenv("VERIFICATION_MODEL") or "openai/gpt-4o-mini"

    def complete(prompt: str) -> str:
        import requests  # local import: keeps the module importable without requests

        response = requests.post(
            f"{base_url.rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0,
                "max_tokens": 400,
            },
            timeout=90,
        )
        response.raise_for_status()
        return response.json()["choices"][0]["message"]["content"]

    return LLMJudge(complete=complete, name=f"llm:{model}")
