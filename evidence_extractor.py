"""
Evidence Extractor Module - compact evidence table from raw research results.

One cheap LLM call per research topic converts raw scraped results into a
compact list of atomic claims bound to numbered sources:

    [{"claim": str, "source_id": int, "quote": str, "confidence": str}, ...]

Sources are numbered once (source_id = 1-based index into the research data)
so downstream inline [n] citations can bind claims to specific sources.

If no LLM is configured or parsing fails, a deterministic sentence-level
fallback keeps drafting unblocked (and makes tests runnable without API keys).
"""

import json
import logging
import os
import re
import time
from typing import List, Dict, Any

from dotenv import load_dotenv
from langchain_openai import ChatOpenAI

load_dotenv()

logging.basicConfig(
    filename="research_agent.log",
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)

# Redact API-key-shaped secrets from everything written to the log
from log_redaction import install_redaction_filter
install_redaction_filter()

# Lazy LLM init so missing keys don't crash on import
llm = None
_llm_cfg = {"api_key": None, "base_url": None, "model": None}

# Hard cap on extracted claims per topic (token efficiency on the FREE tier)
MAX_CLAIMS = 40
# Cap on quote length kept in the table
MAX_QUOTE_CHARS = 200
# Cap on per-source content fed to the extraction prompt
MAX_SOURCE_CHARS = 1500

VALID_CONFIDENCE = {"high", "medium", "low"}


def _env_int(name: str, default: int) -> int:
    """Read a positive int from the environment, falling back to default."""
    try:
        value = int(os.getenv(name, str(default)))
        return value if value > 0 else default
    except ValueError:
        return default


# Reliability: explicit socket timeout + client-side retries (same env vars
# and defaults as draft_agent so behavior is uniform across LLM clients).
DEFAULT_LLM_TIMEOUT_SECONDS = 60
DEFAULT_LLM_MAX_RETRIES = 2
# Cost control: hard cap on completion size (same env var as draft_agent).
DEFAULT_LLM_MAX_OUTPUT_TOKENS = 4000


def _get_llm():
    """Return a ChatOpenAI client for the cheap extraction model, or None.

    Mirrors draft_agent._get_llm but is a separate client/model so the
    extraction pass can use a cheaper FREE-tier model than drafting.
    """
    global llm, _llm_cfg
    api_key = os.getenv("OPENROUTER_API_KEY") or os.getenv("OPENAI_API_KEY")
    if not api_key:
        return None
    base_url = os.getenv("OPENAI_BASE_URL") or "https://openrouter.ai/api/v1"
    model = os.getenv("EVIDENCE_EXTRACTOR_MODEL", "meta-llama/llama-3.3-70b-instruct:free")

    desired = {"api_key": api_key, "base_url": base_url, "model": model}
    if llm is None or any(_llm_cfg.get(k) != v for k, v in desired.items()):
        try:
            llm = ChatOpenAI(
                api_key=api_key,
                base_url=base_url,
                model=model,
                timeout=_env_int("LLM_TIMEOUT_SECONDS", DEFAULT_LLM_TIMEOUT_SECONDS),
                max_retries=_env_int("LLM_MAX_RETRIES", DEFAULT_LLM_MAX_RETRIES),
                max_tokens=_env_int("LLM_MAX_OUTPUT_TOKENS", DEFAULT_LLM_MAX_OUTPUT_TOKENS),
            )
            _llm_cfg = desired
        except Exception as e:
            logging.error(f"Failed to initialize evidence extractor LLM: {type(e).__name__}: {str(e)}")
            llm = None
    return llm


def build_evidence_prompt(data: List[Dict[str, Any]]) -> str:
    """Build the single extraction prompt: numbered sources + output contract."""
    source_blocks = []
    for idx, item in enumerate(data, 1):
        content = re.sub(r"\s+", " ", str(item.get("content", ""))).strip()[:MAX_SOURCE_CHARS]
        title = str(item.get("title", f"Source {idx}")).strip()
        source_blocks.append(f"[{idx}] {title}\n{content}")

    return (
        "You are an evidence extraction engine. Below are numbered sources for one "
        "research topic. Extract the key factual claims as a COMPACT evidence table.\n\n"
        "Rules:\n"
        "- Output ONLY a JSON array, no prose, no markdown fences.\n"
        "- Each element: {\"claim\": short factual statement, \"source_id\": <source number>, "
        "\"quote\": <=20-word verbatim snippet supporting the claim, \"confidence\": \"high\"|\"medium\"|\"low\"}.\n"
        "- Maximum 3 claims per source; skip filler, ads, and navigation text.\n"
        "- source_id MUST match the [n] label of the source the claim came from.\n\n"
        "SOURCES:\n" + "\n\n".join(source_blocks)
    )


def _parse_evidence_json(text: str) -> List[Dict[str, Any]]:
    """Parse the LLM response into a validated evidence list.

    Raises ValueError when no JSON array can be recovered.
    """
    cleaned = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    # Strip markdown code fences if present
    fence = re.search(r"\[.*\]", cleaned, flags=re.DOTALL)
    if fence:
        cleaned = fence.group(0)
    items = json.loads(cleaned)
    if not isinstance(items, list):
        raise ValueError("evidence extractor output is not a JSON array")
    return items


def _normalize_entries(items: List[Any], num_sources: int) -> List[Dict[str, Any]]:
    """Coerce raw parsed entries into valid evidence dicts; drop invalid ones."""
    evidence = []
    for entry in items:
        if not isinstance(entry, dict):
            continue
        claim = str(entry.get("claim", "")).strip()
        if not claim:
            continue
        try:
            source_id = int(entry.get("source_id"))
        except (TypeError, ValueError):
            continue
        if not 1 <= source_id <= num_sources:
            continue
        quote = str(entry.get("quote", "")).strip()[:MAX_QUOTE_CHARS]
        confidence = str(entry.get("confidence", "medium")).strip().lower()
        if confidence not in VALID_CONFIDENCE:
            confidence = "medium"
        evidence.append(
            {"claim": claim, "source_id": source_id, "quote": quote, "confidence": confidence}
        )
        if len(evidence) >= MAX_CLAIMS:
            break
    return evidence


def _fallback_evidence(data: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Deterministic no-LLM extraction: leading sentences become low-confidence claims."""
    evidence = []
    for idx, item in enumerate(data, 1):
        content = re.sub(r"\s+", " ", str(item.get("content", ""))).strip()
        sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", content) if s.strip()]
        for sentence in sentences[:3]:
            if len(sentence) < 25:
                continue
            evidence.append(
                {
                    "claim": sentence[:MAX_QUOTE_CHARS],
                    "source_id": idx,
                    "quote": sentence[:MAX_QUOTE_CHARS],
                    "confidence": "low",
                }
            )
    return evidence[:MAX_CLAIMS]


def extract_evidence(
    data: List[Dict[str, Any]], retries: int = 2, delay: int = 3
) -> List[Dict[str, Any]]:
    """Run ONE extraction call per research topic; fall back deterministically."""
    if not data:
        return []

    llm_local = _get_llm()
    if llm_local:
        prompt = build_evidence_prompt(data)
        last_error = None
        for attempt in range(retries):
            try:
                response = llm_local.invoke([{"role": "user", "content": prompt}])
                items = _parse_evidence_json(response.content)
                evidence = _normalize_entries(items, num_sources=len(data))
                if evidence:
                    return evidence
                last_error = ValueError("empty evidence table from LLM")
            except Exception as e:
                last_error = e
                if attempt < retries - 1:
                    time.sleep(delay)
        logging.warning(
            f"Evidence extraction failed after {retries} attempts, using fallback: "
            f"{type(last_error).__name__}: {str(last_error)}"
        )
    else:
        logging.info("No LLM available for evidence extraction; using deterministic fallback")

    return _fallback_evidence(data)


def render_evidence_table(
    evidence: List[Dict[str, Any]], data: List[Dict[str, Any]]
) -> str:
    """Render the compact evidence block injected into every section prompt.

    Replaces the old raw json.dumps(data) dump (which duplicated all source
    content up to 6x in deep mode).
    """
    if not evidence and not data:
        return ""

    lines = ["Numbered sources:"]
    for idx, item in enumerate(data, 1):
        title = str(item.get("title", f"Source {idx}")).strip()
        url = str(item.get("url", "")).strip()
        lines.append(f"[{idx}] {title}" + (f" ({url})" if url else ""))

    if evidence:
        lines.append("")
        lines.append("Evidence table:")
        for e in evidence:
            parts = [f"- {e['claim']}"]
            if e.get("quote"):
                parts.append(f'Quote: "{e["quote"]}"')
            parts.append(f"(source [{e['source_id']}], confidence: {e['confidence']})")
            lines.append(" ".join(parts))
    else:
        lines.append("")
        lines.append("No structured evidence extracted; rely on the source titles above.")

    return "\n".join(lines)
