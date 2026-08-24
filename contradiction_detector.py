"""
Contradiction Surfacing - detect conflicting claims across sources/sections.

Rather than silently averaging conflicting information, one LLM call per
research topic compares the evidence table rows and flags pairs of claims
that cannot both be true:

    [{"claim_a": str, "source_id_a": int,
      "claim_b": str, "source_id_b": int,
      "topic": str, "severity": "high"|"medium"|"low"}, ...]

If no LLM is configured or parsing fails, a lightweight deterministic
detector (numeric/negation disagreement on the same subject) runs instead,
so report generation is never blocked (and tests run without API keys).
Surfaced contradictions are rendered as an explicit block appended to the
report output — never averaged away.
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

VALID_SEVERITIES = {"high", "medium", "low"}


def resolve_contradiction_language(language: str = None) -> str | None:
    """Pick the language the detector should reason in.

    Priority: explicit `language` argument (the draft's language param) >
    CONTRADICTION_LANGUAGE env var > None (unspecified; prompt stays in
    English). Severity enums remain canonical English regardless.
    """
    lang = (language or "").strip() or os.getenv("CONTRADICTION_LANGUAGE", "").strip()
    return lang or None

# Reliability: explicit socket timeout + client-side retries (same env vars
# and defaults as draft_agent so behavior is uniform across LLM clients).
DEFAULT_LLM_TIMEOUT_SECONDS = 60
DEFAULT_LLM_MAX_RETRIES = 2
# Cost control: hard cap on completion size (same env var as draft_agent).
DEFAULT_LLM_MAX_OUTPUT_TOKENS = 4000


def _env_int(name: str, default: int) -> int:
    """Read a positive int from the environment, falling back to default."""
    try:
        value = int(os.getenv(name, str(default)))
        return value if value > 0 else default
    except ValueError:
        return default


def _get_llm():
    """Return a ChatOpenAI client for the contradiction model, or None.

    Mirrors draft_agent._get_llm but uses its own model env var so the
    detection pass can run on a different (e.g. cheaper/free) model.
    """
    global llm, _llm_cfg
    api_key = os.getenv("OPENROUTER_API_KEY") or os.getenv("OPENAI_API_KEY")
    if not api_key:
        return None
    base_url = os.getenv("OPENAI_BASE_URL") or "https://openrouter.ai/api/v1"
    model = os.getenv(
        "CONTRADICTION_MODEL", "meta-llama/llama-3.3-70b-instruct:free"
    )

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
            logging.error(f"Failed to initialize contradiction LLM: {type(e).__name__}: {str(e)}")
            llm = None
    return llm


def build_contradiction_prompt(
    data: List[Dict[str, Any]],
    language: str | None = None,
) -> str:
    """Build the single detection prompt: numbered sources + output contract.

    When `language` is set (the draft's language or CONTRADICTION_LANGUAGE),
    an explicit instruction tells the detector to quote claims and describe
    topics in that language; severity enums stay canonical English.
    """
    source_blocks = []
    for idx, item in enumerate(data, 1):
        content = re.sub(r"\s+", " ", str(item.get("content", ""))).strip()[:1500]
        title = str(item.get("title", f"Source {idx}")).strip()
        source_blocks.append(f"[{idx}] {title}\n{content}")

    lang_instruction = ""
    if language:
        lang_instruction = (
            f"\n- Write claim_a, claim_b and topic in {language}. The "
            f"\"severity\" field MUST still use exactly one of the English "
            f"enum values above.\n"
        )

    return (
        "You are a contradiction detector. Below are numbered sources for one "
        "research topic. Identify pairs of factual claims that CONFLICT — i.e. "
        "cannot both be true at the same time.\n\n"
        "Rules:\n"
        "- Output ONLY a JSON array, no prose, no markdown fences.\n"
        '- Each element: {"claim_a": short quote of the first claim, '
        '"source_id_a": <number>, "claim_b": short quote of the conflicting claim, '
        '"source_id_b": <number>, "topic": what the dispute is about, '
        '"severity": "high"|"medium"|"low"}.\n'
        "- source_id_a / source_id_b MUST be the [n] labels of the sources.\n"
        "- Only report genuine factual conflicts; mere differences in emphasis "
        "or scope are NOT contradictions.\n"
        "- If there are no conflicts, output [].\n"
        f"{lang_instruction}\n"
        "SOURCES:\n" + "\n\n".join(source_blocks)
    )


def _parse_contradictions_json(text: str) -> List[Dict[str, Any]]:
    """Parse the LLM response into a list of contradiction dicts.

    Raises ValueError when no JSON array can be recovered.
    """
    cleaned = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    fence = re.search(r"\[.*\]", cleaned, flags=re.DOTALL)
    if fence:
        cleaned = fence.group(0)
    items = json.loads(cleaned)
    if not isinstance(items, list):
        raise ValueError("contradiction output is not a JSON array")
    return items


def _normalize_contradictions(items: List[Any], num_sources: int) -> List[Dict[str, Any]]:
    """Coerce raw parsed entries into valid dicts; drop invalid ones."""
    contradictions = []
    for entry in items:
        if not isinstance(entry, dict):
            continue
        try:
            id_a = int(entry.get("source_id_a"))
            id_b = int(entry.get("source_id_b"))
        except (TypeError, ValueError):
            continue
        if not (1 <= id_a <= num_sources and 1 <= id_b <= num_sources):
            continue
        claim_a = re.sub(r"\s+", " ", str(entry.get("claim_a", ""))).strip()
        claim_b = re.sub(r"\s+", " ", str(entry.get("claim_b", ""))).strip()
        if not claim_a or not claim_b:
            continue
        severity = str(entry.get("severity", "medium")).strip().lower()
        if severity not in VALID_SEVERITIES:
            severity = "medium"
        contradictions.append({
            "claim_a": claim_a,
            "source_id_a": id_a,
            "claim_b": claim_b,
            "source_id_b": id_b,
            "topic": re.sub(r"\s+", " ", str(entry.get("topic", ""))).strip(),
            "severity": severity,
        })
    return contradictions


# --- Deterministic fallback detector ---------------------------------------

_NEGATION_RE = re.compile(
    r"\b(no|not|never|decline|declined|declining|drop|dropped|dropping|"
    r"fall|fell|falling|reduce|reduced|reducing|decrease|decreased|"
    r"lower|lowered|slower)\b",
    re.IGNORECASE,
)
_INCREASE_RE = re.compile(
    r"\b(increase|increased|increasing|rise|rose|risen|rising|grow|grew|"
    r"growing|higher|faster|improve|improved|more)\b",
    re.IGNORECASE,
)


def _sentences(text: str) -> List[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if len(s.strip()) > 20]


def detect_contradictions_heuristic(data: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Lightweight offline detector: numeric or directional disagreement.

    Flags sentence pairs that (a) share a salient content word and (b)
    disagree either in numbers or in trend direction. Far noisier than the
    LLM judge but deterministic and dependency-free.
    """
    sentences = []
    for idx, item in enumerate(data, 1):
        for sent in _sentences(str(item.get("content", ""))):
            sentences.append({"text": sent, "source_id": idx})

    contradictions = []
    seen_pairs = set()
    for i in range(len(sentences)):
        for j in range(i + 1, len(sentences)):
            a, b = sentences[i], sentences[j]
            words_a = set(w.lower() for w in re.findall(r"[a-z]{4,}", a["text"]))
            words_b = set(w.lower() for w in re.findall(r"[a-z]{4,}", b["text"]))
            shared = words_a & words_b - {
                "this", "that", "with", "from", "have", "been", "were", "which"
            }
            if not shared:
                continue
            nums_a = set(re.findall(r"\d+(?:\.\d+)?%?", a["text"]))
            nums_b = set(re.findall(r"\d+(?:\.\d+)?%?", b["text"]))
            numeric_conflict = bool(nums_a and nums_b and nums_a != nums_b)
            dir_conflict = (
                bool(_INCREASE_RE.search(a["text"]) and _NEGATION_RE.search(b["text"]))
                or bool(_NEGATION_RE.search(a["text"]) and _INCREASE_RE.search(b["text"]))
            )
            if not (numeric_conflict or dir_conflict):
                continue
            key = tuple(sorted((a["text"], b["text"])))
            if key in seen_pairs:
                continue
            seen_pairs.add(key)
            contradictions.append({
                "claim_a": a["text"],
                "source_id_a": a["source_id"],
                "claim_b": b["text"],
                "source_id_b": b["source_id"],
                "topic": "numeric disagreement" if numeric_conflict else "trend direction",
                "severity": "low",
                "heuristic": True,
            })
            if len(contradictions) >= 10:
                return contradictions
    return contradictions


def detect_contradictions(
    data: List[Dict[str, Any]], retries: int = 2, delay: int = 3,
    language: str | None = None,
) -> List[Dict[str, Any]]:
    """Run ONE detection call per research topic; fall back deterministically.

    `language` (or the CONTRADICTION_LANGUAGE env var) makes the detector
    quote claims in the report's language; severity enums stay canonical.
    """
    if not data:
        return []

    llm_local = _get_llm()
    if llm_local:
        prompt = build_contradiction_prompt(
            data, language=resolve_contradiction_language(language)
        )
        last_error = None
        for attempt in range(retries):
            try:
                response = llm_local.invoke([{"role": "user", "content": prompt}])
                items = _parse_contradictions_json(response.content)
                return _normalize_contradictions(items, num_sources=len(data))
            except Exception as e:
                last_error = e
                if attempt < retries - 1:
                    time.sleep(delay)
        logging.warning(
            f"Contradiction detection failed after {retries} attempts, using "
            f"heuristic fallback: {type(last_error).__name__}: {str(last_error)}"
        )
    else:
        logging.info(
            "No LLM available for contradiction detection; using heuristic fallback"
        )

    return detect_contradictions_heuristic(data)


def render_contradictions_block(contradictions: List[Dict[str, Any]]) -> str:
    """Render surfaced contradictions as an explicit Markdown block appended
    to the report output — conflicts are shown to the reader, never averaged."""
    if not contradictions:
        return ""
    lines = ["## Conflicting Evidence"]
    lines.append(
        f"{len(contradictions)} conflict(s) were detected between sources. "
        "They are listed explicitly rather than averaged."
    )
    for c in contradictions:
        tag = " *(heuristic detection)*" if c.get("heuristic") else ""
        lines.append(
            f"- **{c['severity'].upper()}** — {c['topic']}{tag}\n"
            f'  - Source [{c["source_id_a"]}]: "{c["claim_a"]}"\n'
            f'  - Source [{c["source_id_b"]}]: "{c["claim_b"]}"'
        )
    return "\n".join(lines)
