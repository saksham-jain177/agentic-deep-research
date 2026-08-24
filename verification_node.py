"""
Verification Node - LLM-as-a-Judge pass over generated report sections.

One verification LLM call per report section checks the drafted claims
against the evidence table / gathered sources and returns a structured
verdict:

    {
      "section": str,
      "verdict": "supported" | "partially_supported" | "unsupported",
      "unsupported_claims": [str, ...],
      "citations_coverage": float   # fraction of paragraphs carrying an [n] citation
    }

If no LLM is configured or parsing fails, a deterministic heuristic fallback
scores each section from citation coverage alone, so report generation is
never blocked (and tests run without API keys).
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

VALID_VERDICTS = {"supported", "partially_supported", "unsupported"}


def resolve_verification_language(language: str = None) -> str | None:
    """Pick the language the judge should reason in.

    Priority: explicit `language` argument (the draft's language param) >
    VERIFICATION_LANGUAGE env var > None (unspecified; prompt stays in
    English). Verdict enums remain canonical English regardless.
    """
    lang = (language or "").strip() or os.getenv("VERIFICATION_LANGUAGE", "").strip()
    return lang or None


def resolve_contradiction_language(language: str = None) -> str | None:
    """Same resolution order for the contradiction detector, using
    CONTRADICTION_LANGUAGE as its env override."""
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
    """Return a ChatOpenAI client for the judge model, or None.

    Mirrors draft_agent._get_llm but uses its own model env var so the
    judge pass can run on a different (e.g. cheaper/free) model.
    """
    global llm, _llm_cfg
    api_key = os.getenv("OPENROUTER_API_KEY") or os.getenv("OPENAI_API_KEY")
    if not api_key:
        return None
    base_url = os.getenv("OPENAI_BASE_URL") or "https://openrouter.ai/api/v1"
    model = os.getenv("VERIFICATION_MODEL", "meta-llama/llama-3.3-70b-instruct:free")

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
            logging.error(f"Failed to initialize verification LLM: {type(e).__name__}: {str(e)}")
            llm = None
    return llm


def build_verification_prompt(
    section_title: str,
    section_text: str,
    data: List[Dict[str, Any]],
    language: str | None = None,
) -> str:
    """Build the judge prompt: numbered sources + the section under review.

    When `language` is set (the draft's language or VERIFICATION_LANGUAGE),
    an explicit instruction tells the judge to reason in that language;
    verdict enums stay canonical English either way.
    """
    source_blocks = []
    for idx, item in enumerate(data, 1):
        content = re.sub(r"\s+", " ", str(item.get("content", ""))).strip()[:1500]
        title = str(item.get("title", f"Source {idx}")).strip()
        source_blocks.append(f"[{idx}] {title}\n{content}")

    lang_instruction = ""
    if language:
        lang_instruction = (
            f"\n- Write your reasoning, notes and any unsupported_claims "
            f"quotes in {language}. The \"verdict\" field MUST still use "
            f"exactly one of the English enum values above.\n"
        )

    return (
        "You are a strict fact-verification judge. Below are numbered sources "
        "and ONE drafted report section. Verify every factual claim in the "
        "section against the sources.\n\n"
        "Rules:\n"
        "- Output ONLY a JSON object, no prose, no markdown fences.\n"
        '- Shape: {"verdict": "supported"|"partially_supported"|"unsupported", '
        '"unsupported_claims": [short quotes of claims not backed by any source], '
        '"notes": one-sentence justification}.\n'
        '- "supported": every factual claim traces to a numbered source.\n'
        '- "partially_supported": some claims are backed, others are not.\n'
        '- "unsupported": most claims have no source backing.\n'
        "- Judge ONLY against the sources given; do not use outside knowledge.\n"
        f"{lang_instruction}\n"
        f'SECTION TITLE: {section_title}\n\n'
        f"SECTION TEXT:\n{section_text}\n\n"
        "SOURCES:\n" + "\n\n".join(source_blocks)
    )


def _parse_verdict_json(text: str) -> Dict[str, Any]:
    """Parse the LLM response into a validated verdict dict.

    Raises ValueError when no JSON object can be recovered.
    """
    cleaned = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    fence = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
    if fence:
        cleaned = fence.group(0)
    obj = json.loads(cleaned)
    if not isinstance(obj, dict):
        raise ValueError("verification output is not a JSON object")
    return obj


def _normalize_verdict(obj: Any, section_title: str, citations_coverage: float) -> Dict[str, Any]:
    """Coerce a raw parsed verdict into the canonical shape; None if invalid."""
    if not isinstance(obj, dict):
        return None
    verdict = re.sub(r"[\s-]+", "_", str(obj.get("verdict", "")).strip().lower())
    if verdict not in VALID_VERDICTS:
        return None
    claims = obj.get("unsupported_claims")
    if not isinstance(claims, list):
        claims = []
    unsupported = [
        re.sub(r"\s+", " ", str(c)).strip()
        for c in claims
        if str(c).strip()
    ]
    return {
        "section": section_title,
        "verdict": verdict,
        "unsupported_claims": unsupported,
        "citations_coverage": round(citations_coverage, 2),
        "notes": str(obj.get("notes", "")).strip(),
    }


def compute_citations_coverage(section_text: str) -> float:
    """Fraction of non-empty paragraphs that carry at least one inline [n] ref."""
    paragraphs = [p.strip() for p in section_text.split("\n\n") if p.strip()]
    if not paragraphs:
        return 1.0
    cited = sum(1 for p in paragraphs if re.search(r"\[\d+\]", p))
    return cited / len(paragraphs)


def verify_section(
    section_title: str,
    section_text: str,
    data: List[Dict[str, Any]],
    retries: int = 2,
    delay: int = 3,
    language: str | None = None,
) -> Dict[str, Any]:
    """Run ONE judge call per section; fall back deterministically.

    `language` (or the VERIFICATION_LANGUAGE env var) makes the judge
    reason in the report's language; verdict enums stay canonical English.
    """
    coverage = compute_citations_coverage(section_text)

    llm_local = _get_llm()
    if llm_local:
        prompt = build_verification_prompt(
            section_title, section_text, data,
            language=resolve_verification_language(language),
        )
        last_error = None
        for attempt in range(retries):
            try:
                response = llm_local.invoke([{"role": "user", "content": prompt}])
                verdict = _normalize_verdict(
                    _parse_verdict_json(response.content), section_title, coverage
                )
                if verdict:
                    return verdict
                last_error = ValueError("invalid or empty verdict from LLM")
            except Exception as e:
                last_error = e
                if attempt < retries - 1:
                    time.sleep(delay)
        logging.warning(
            f"Verification failed after {retries} attempts for section "
            f"{section_title}, using heuristic fallback: "
            f"{type(last_error).__name__}: {str(last_error)}"
        )
    else:
        logging.info(
            f"No LLM available for verification; using heuristic fallback for {section_title}"
        )

    return heuristic_verdict(section_title, section_text, coverage)


def heuristic_verdict(
    section_title: str, section_text: str, coverage: float | None = None
) -> Dict[str, Any]:
    """Deterministic fallback: judge only by inline-citation coverage.

    Never blocks report generation; used when no LLM is configured or the
    judge call failed. Unsupported claims cannot be identified without an
    LLM, so the list stays empty and the verdict is conservative.
    """
    if coverage is None:
        coverage = compute_citations_coverage(section_text)
    if coverage >= 0.8:
        verdict = "supported"
    elif coverage > 0.0:
        verdict = "partially_supported"
    else:
        verdict = "unsupported"
    return {
        "section": section_title,
        "verdict": verdict,
        "unsupported_claims": [],
        "citations_coverage": round(coverage, 2),
        "notes": "Heuristic verdict based on citation coverage only (no LLM judge).",
    }


def verify_report(
    sections: List[Dict[str, Any]],
    data: List[Dict[str, Any]],
    language: str | None = None,
) -> List[Dict[str, Any]]:
    """Verify every section of a generated report; never raises.

    `language` is threaded from the draft's language param so verdicts are
    judged in the report's language (enums stay canonical English).
    """
    results = []
    for section in sections:
        title = str(section.get("title", "Untitled")).strip() or "Untitled"
        text = str(section.get("content", ""))
        try:
            results.append(verify_section(title, text, data, language=language))
        except Exception as e:
            logging.warning(
                f"Verification crashed for section {title} ({type(e).__name__}: {e}); "
                "using heuristic fallback"
            )
            results.append(heuristic_verdict(title, text))
    return results


def render_verification_summary(verifications: List[Dict[str, Any]]) -> str:
    """Render per-section verification verdicts as a Markdown block appended
    to the report output, so contradictions/verdicts are explicit to readers."""
    if not verifications:
        return ""
    lines = ["## Verification Summary"]
    overall_supported = sum(1 for v in verifications if v["verdict"] == "supported")
    lines.append(
        f"{overall_supported}/{len(verifications)} sections fully supported by gathered sources."
    )
    for v in verifications:
        line = f"- **{v['section']}**: {v['verdict']} (citation coverage: {v['citations_coverage']:.0%})"
        if v.get("unsupported_claims"):
            line += " — unverified claims:"
            lines.append(line)
            for claim in v["unsupported_claims"]:
                lines.append(f"  - \"{claim}\"")
        else:
            lines.append(line)
    return "\n".join(lines)
