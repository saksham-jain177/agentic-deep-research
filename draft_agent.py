import os
import json
import time
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from langchain.prompts import PromptTemplate
from langchain.tools import StructuredTool
import requests
from openai import APIConnectionError, RateLimitError
import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Dict, Any

import evidence_extractor
import cost_estimator
# LangChain 0.1.x StructuredTool requires a pydantic.v1 schema; use it even
# when pydantic v2 is installed (v2 installs expose the v1 compat layer).
try:
    from pydantic.v1 import BaseModel, Field
except ImportError:  # pydantic < 2 installed
    from pydantic import BaseModel, Field
from urllib.parse import urlparse
from datetime import date, datetime

# Set up logging
logging.basicConfig(filename="research_agent.log", level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# Redact API-key-shaped secrets from everything written to the log
from log_redaction import install_redaction_filter
install_redaction_filter()

# Load environment variables from .env
load_dotenv()

# Lazy LLM init so missing keys don't crash on import
llm = None
_llm_cfg = {"api_key": None, "base_url": None, "model": None}

def _env_int(name: str, default: int) -> int:
    """Read a positive int from the environment, falling back to default."""
    try:
        value = int(os.getenv(name, str(default)))
        return value if value > 0 else default
    except ValueError:
        return default

# Reliability: every LLM HTTP call gets an explicit socket timeout and client-
# side retry budget instead of hanging indefinitely (env-configurable).
DEFAULT_LLM_TIMEOUT_SECONDS = 60
DEFAULT_LLM_MAX_RETRIES = 2
# Cost control: hard cap on completion size for every ChatOpenAI client.
DEFAULT_LLM_MAX_OUTPUT_TOKENS = 4000


def _llm_network_kwargs() -> dict:
    """Shared ChatOpenAI kwargs: request timeout + client-side retries."""
    return {
        "timeout": _env_int("LLM_TIMEOUT_SECONDS", DEFAULT_LLM_TIMEOUT_SECONDS),
        "max_retries": _env_int("LLM_MAX_RETRIES", DEFAULT_LLM_MAX_RETRIES),
        "max_tokens": _env_int("LLM_MAX_OUTPUT_TOKENS", DEFAULT_LLM_MAX_OUTPUT_TOKENS),
    }

def _get_llm():
    """Return a configured ChatOpenAI instance or None if keys are missing.
    Accepts either OPENROUTER_API_KEY (preferred) or OPENAI_API_KEY with
    OPENAI_BASE_URL. Defaults base_url to OpenRouter if not provided.
    """
    global llm, _llm_cfg
    api_key = os.getenv("OPENROUTER_API_KEY") or os.getenv("OPENAI_API_KEY")
    if not api_key:
        return None
    base_url = os.getenv("OPENAI_BASE_URL") or "https://openrouter.ai/api/v1"
    model = os.getenv("OPENROUTER_MODEL", "tngtech/deepseek-r1t2-chimera:free")

    desired = {"api_key": api_key, "base_url": base_url, "model": model}
    # Rebuild the LLM client if config changed (e.g., user switched models)
    if llm is None or any(_llm_cfg.get(k) != v for k, v in desired.items()):
        try:
            llm = ChatOpenAI(api_key=api_key, base_url=base_url, model=model, **_llm_network_kwargs())
            _llm_cfg = desired
        except Exception as e:
            logging.error(f"Failed to initialize LLM: {type(e).__name__}: {str(e)}")
            llm = None
    return llm

def _get_fallback_models():
    """Parse OPENROUTER_FALLBACK_MODELS (comma-separated) into a clean list."""
    models = []
    for model in os.getenv("OPENROUTER_FALLBACK_MODELS", "").split(","):
        model = model.strip()
        if model and model not in models:
            models.append(model)
    return models

def _llm_for_model(model: str):
    """Build a ChatOpenAI client for a specific model in the fallback chain."""
    api_key = os.getenv("OPENROUTER_API_KEY") or os.getenv("OPENAI_API_KEY")
    base_url = os.getenv("OPENAI_BASE_URL") or "https://openrouter.ai/api/v1"
    return ChatOpenAI(api_key=api_key, base_url=base_url, model=model, **_llm_network_kwargs())

def _should_advance_model_chain(error: Exception) -> bool:
    """True for rate-limit / empty-response failures worth a different model.

    Any other error (auth, network) stays on the current model and uses the
    plain retry loop instead.
    """
    message = str(error).lower()
    return (
        isinstance(error, RateLimitError)
        or "429" in str(error)
        or "rate limit" in message
        or "empty response" in message
    )

# --- Token budget guard (cost control) ---
# Projected input tokens for one full report (evidence table x sections +
# template overhead) must stay under this cap. Override with the
# TOKEN_BUDGET_PER_REPORT env var. When over budget, lowest-confidence
# evidence rows are trimmed first until the projection fits.
DEFAULT_TOKEN_BUDGET_PER_REPORT = 12000

_CONFIDENCE_RANK = {"low": 0, "medium": 1, "high": 2}


def get_token_budget() -> int:
    """Read TOKEN_BUDGET_PER_REPORT from the environment, with safe default."""
    raw = os.getenv("TOKEN_BUDGET_PER_REPORT")
    if raw is None:
        return DEFAULT_TOKEN_BUDGET_PER_REPORT
    try:
        value = int(raw)
        if value <= 0:
            raise ValueError
        return value
    except ValueError:
        logging.warning(f"Invalid TOKEN_BUDGET_PER_REPORT={raw!r}; using default {DEFAULT_TOKEN_BUDGET_PER_REPORT}")
        return DEFAULT_TOKEN_BUDGET_PER_REPORT


def _projected_input_tokens(evidence, data, deep_research, num_sections) -> int:
    """Estimate total input tokens for drafting: evidence table per section."""
    table_str = evidence_extractor.render_evidence_table(evidence, data)
    mode = "deep" if deep_research else "shallow"
    overhead = cost_estimator.TEMPLATE_OVERHEAD[mode] * max(num_sections, 1)
    return cost_estimator.count_text_tokens(table_str) + overhead


def enforce_token_budget(
    evidence: List[Dict[str, Any]],
    data: List[Dict[str, Any]],
    deep_research: bool,
    num_sections: int,
    budget: int = None,
):
    """Trim lowest-confidence evidence rows until the projected prompt fits.

    Rows are dropped one at a time, lowest confidence first (low -> medium ->
    high; ties broken by original order), re-projecting after each drop.
    Returns (trimmed_evidence, info_dict) where info reports how many rows
    were dropped and the final projection.
    """
    if budget is None:
        budget = get_token_budget()
    trimmed = list(evidence or [])
    dropped = []
    projected = _projected_input_tokens(trimmed, data, deep_research, num_sections)

    while projected > budget and trimmed:
        min_rank = min(_CONFIDENCE_RANK.get(row.get("confidence", "medium"), 1) for row in trimmed)
        idx = next(
            i for i, row in enumerate(trimmed)
            if _CONFIDENCE_RANK.get(row.get("confidence", "medium"), 1) == min_rank
        )
        dropped.append(trimmed.pop(idx))
        projected = _projected_input_tokens(trimmed, data, deep_research, num_sections)

    if dropped:
        logging.info(
            f"Token budget {budget}: trimmed {len(dropped)} low-confidence evidence "
            f"row(s); projected input tokens now ~{projected}"
        )
    info = {"budget": budget, "dropped": len(dropped), "dropped_rows": dropped, "projected_tokens": projected}
    return trimmed, info


# Writing style templates following industry standards
STYLE_TEMPLATES = {
    "academic": {
        "tone": "formal, objective, and evidence-based",
        "vocabulary": "discipline-specific terminology, passive voice where appropriate, third-person perspective",
        "structure": "introduction with thesis, literature review, methodology, findings, discussion, conclusion with citations throughout"
    },
    "business": {
        "tone": "professional, concise, and results-focused",
        "vocabulary": "industry jargon, active voice, direct address, action verbs",
        "structure": "executive summary, key findings upfront, data-driven insights, clear recommendations, ROI focus"
    },
    "technical": {
        "tone": "precise, detailed, and systematic",
        "vocabulary": "technical specifications, exact measurements, specialized terminology, unambiguous language",
        "structure": "abstract, introduction, technical background, implementation details, results, performance metrics, conclusion"
    },
    "casual": {
        "tone": "conversational, engaging, and reader-friendly",
        "vocabulary": "simple language, contractions allowed, personal pronouns, relatable examples",
        "structure": "hook introduction, storytelling elements, short paragraphs, bullet points, clear takeaways"
    }
}

# Note: Citation formatting function moved below with improved standards

def apply_writing_style(prompt: str, style: str) -> str:
    """Apply writing style to prompt template."""
    style_config = STYLE_TEMPLATES.get(style, STYLE_TEMPLATES["academic"])
    return prompt + f"\n\nUse a {style_config['tone']} tone with {style_config['vocabulary']}, following a {style_config['structure']}."

# Prompts for each section in deep research mode
def get_deep_word_counts(target_word_count):
    """Distribute the target word count across sections in deep mode."""
    abstract = max(150, int(target_word_count * 0.05))  # 5%
    intro = max(400, int(target_word_count * 0.15))  # 15%
    lit_review = max(600, int(target_word_count * 0.20))  # 20%
    findings = max(800, int(target_word_count * 0.30))  # 30%
    analysis = max(800, int(target_word_count * 0.20))  # 20%
    conclusion = max(400, int(target_word_count * 0.10))  # 10%
    return abstract, intro, lit_review, findings, analysis, conclusion

abstract_prompt = PromptTemplate(
    input_variables=["data", "word_count"],
    template="""
    Generate a detailed abstract for a research paper based on the following data. Provide a comprehensive overview of the topic, research objectives, key findings, and their implications in approximately {word_count} words. Include a brief mention of the methodology and significance of the research. Provide detailed insights and avoid summarizing the data directly—focus on synthesizing the overall narrative. Do not include the word "Abstract" in your response; only provide the content of the abstract section. Do not include any internal reasoning tags like <think> or similar markers in your response; only provide the final content.

    Data: {data}
    """
)

introduction_prompt = PromptTemplate(
    input_variables=["data", "word_count"],
    template="""
    Generate a detailed introduction for a research paper based on the following data. Introduce the topic in depth, covering its historical context, current significance, and the purpose of this research in approximately {word_count} words. Discuss its relevance in scientific, technological, or societal contexts, citing specific trends or events. Elaborate with examples, historical developments, and current challenges in the field. Do not include the word "Introduction" in your response; only provide the content of the introduction section. Do not include any internal reasoning tags like <think> or similar markers in your response; only provide the final content.

    Data: {data}
    """
)

literature_review_prompt = PromptTemplate(
    input_variables=["data", "word_count"],
    template="""
    Generate a detailed literature review for a research paper based on the following data. Synthesize existing knowledge and findings from all provided sources in approximately {word_count} words. Highlight trends, gaps, controversies, and key developments in the field, providing a critical overview of the current state of research. Include specific references to studies or advancements mentioned in the data, and discuss their implications. Do not include the phrase "Literature Review" in your response; only provide the content of the literature review section. Do not include any internal reasoning tags like <think> or similar markers in your response; only provide the final content.

    Data: {data}
    """
)

key_findings_prompt = PromptTemplate(
    input_variables=["data", "word_count"],
    template="""
    Generate a detailed key findings section for a research paper based on the following data. Summarize the main points in a numbered list (5-7 points, approximately {word_count} words total), including specific examples, data points, and insights from each source where applicable. Ensure comprehensive coverage of all relevant findings, discussing methodologies, results, and their significance. Each numbered point must be on a new line with a newline character (\n) between points (e.g., 1. First finding.\n2. Second finding.\n3. Third finding.). Ensure there is a space after each number and period (e.g., "1. " not "1."). Do not include the phrase "Key Findings" in your response; only provide the content of the key findings section. Do not include any internal reasoning tags like <think> or similar markers in your response; only provide the final content.

    Data: {data}
    """
)

analysis_prompt = PromptTemplate(
    input_variables=["data", "word_count"],
    template="""
    Generate a detailed analysis section for a research paper based on the following data. Provide in-depth insights, implications, and critical analysis of the findings in approximately {word_count} words. Discuss broader impacts, potential applications, limitations, challenges, and areas of uncertainty, integrating perspectives from the data. Compare and contrast findings, and propose hypotheses for future exploration. Elaborate extensively with examples and potential scenarios. Do not include the word "Analysis" in your response; only provide the content of the analysis section. Do not include any internal reasoning tags like <think> or similar markers in your response; only provide the final content. Use Markdown tables or lists where appropriate to improve readability.

    Data: {data}
    """
)

conclusion_prompt = PromptTemplate(
    input_variables=["data", "word_count"],
    template="""
    Generate a detailed conclusion section for a research paper based on the following data. Provide a thorough summary of findings, their significance, and potential future developments in approximately {word_count} words. Offer detailed recommendations for further research, addressing how the findings contribute to the field and what steps should be taken next. Discuss long-term implications and future directions. Do not include the word "Conclusion" in your response; only provide the content of the conclusion section. Do not include any internal reasoning tags like <think> or similar markers in your response; only provide the final content.

    Data: {data}
    """
)

# Function to clean <think> tags from text
def clean_think_tags(text):
    """Remove <think> tags and their contents from the text."""
    cleaned_text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    cleaned_text = re.sub(r"</?think>", "", cleaned_text)
    cleaned_text = re.sub(r"\s+", " ", cleaned_text).strip()
    return cleaned_text

# Function to format Key Findings as a proper numbered list
def format_key_findings(text):
    """Format the Key Findings section as a numbered list with each point on a new line."""
    text = re.sub(r"\s+", " ", text.strip())
    points = re.split(r"(?=\d+\.\s?)", text)
    formatted_text = ""
    for point in points:
        point = point.strip()
        if point:
            point = re.sub(r"^(\d+\.)([^\s])", r"\1 \2", point)
            formatted_text += point + "\n"
    formatted_text = formatted_text.strip()
    formatted_text = re.sub(r"\n{2,}", "\n", formatted_text)
    return formatted_text

# Define the schema for StructuredTool arguments using Pydantic
class DraftAnswerArgs(BaseModel):
    data: List[Dict[str, Any]] = Field(description="List of research data dictionaries containing title, content, and url")
    deep_research: bool = Field(default=False, description="Whether to perform deep research mode (detailed summary)")
    target_word_count: int = Field(default=1000, description="Target word count for the summary")
    writing_style: str = Field(default="academic", description="Writing style for the summary")
    citation_format: str = Field(default="APA", description="Citation format for references")
    language: str = Field(default="english", description="Language for the summary")
    retries: int = Field(default=3, description="Number of retries for API calls")
    delay: int = Field(default=5, description="Delay between retries in seconds")

STYLE_PROMPTS = {
    "academic": """Write in a formal academic style with:
        - Scholarly terminology and precise language
        - Clear theoretical foundations
        - Objective analysis
        - Proper citations and references""",
    
    "business": """Write in a professional business style with:
        - Executive summary approach
        - Action-oriented insights
        - Clear ROI and business implications
        - Professional but accessible language""",
    
    "technical": """Write in a technical style with:
        - Detailed technical specifications
        - Step-by-step explanations
        - Technical terminology
        - Data-driven insights""",
    
    "casual": """Write in an accessible, casual style with:
        - Clear, everyday language
        - Engaging examples
        - Conversational tone
        - Relatable explanations"""
}

LANGUAGE_PROMPTS = {
    "english": """Write in standard academic English following international research paper standards:
        - Use British/American English consistently
        - Follow academic writing conventions
        - Maintain formal scholarly tone""",
    
    "spanish": """Escriba en español académico siguiendo los estándares internacionales de investigación:
        - Use español académico estándar
        - Siga las convenciones académicas españolas
        - Mantenga un tono académico formal""",
    
    "french": """Rédigez en français académique selon les normes internationales de recherche:
        - Utilisez le français académique standard
        - Suivez les conventions académiques françaises
        - Maintenez un ton académique formel""",
    
    "german": """Schreiben Sie in akademischem Deutsch nach internationalen Forschungsstandards:
        - Verwenden Sie Standard-Wissenschaftsdeutsch
        - Folgen Sie deutschen akademischen Konventionen
        - Halten Sie einen formellen akademischen Ton""",
    
    "chinese": """按照国际研究论文标准使用学术中文写作：
        - 使用规范的学术中文
        - 遵循中文学术写作规范
        - 保持正式的学术语气""",
}

def _parse_publication_date(value) -> date | None:
    """Defensively coerce a publication date into a `date`.

    Accepts date objects, ISO strings ("2024-01-15"), and bare years
    ("2024"); anything unparseable becomes None so citation formatting
    degrades to (n.d.) instead of crashing.
    """
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        text = value.strip()
        try:
            return date.fromisoformat(text[:10])
        except ValueError:
            match = re.search(r"\d{4}", text)
            if match:
                return date(int(match.group()), 1, 1)
    return None

def format_citation(source_dict: Dict[str, str], style: str) -> str:
    """Format citation based on selected style using the CitationFormatter engine."""
    try:
        from citation_formatter import CitationFormatter, Source

        # Map research-result dict keys to Source fields. Sources built by
        # research_agent carry title/url/content; author info may arrive as a
        # single string ("author") or a list ("authors").
        authors = source_dict.get("authors")
        if not authors:
            authors = source_dict.get("author")
        if not authors:
            authors = None
        elif isinstance(authors, str):
            authors = [authors]

        source = Source(
            title=str(source_dict.get('title') or 'Unknown Title'),
            url=str(source_dict.get('url') or ''),
            authors=authors,
            publisher=source_dict.get('publisher'),
            publication_date=_parse_publication_date(
                source_dict.get('publication_date', source_dict.get('date'))
            ),
        )

        formatter = CitationFormatter([source])
        style_lower = (style or "APA").lower()
        if style_lower == "mla":
            return formatter.format_mla()
        if style_lower == "ieee":
            return formatter.format_ieee()
        if style_lower == "bibtex":
            return formatter.format_bibtex()
        return formatter.format_apa()
    except Exception as e:
        logging.error(f"Error in professional citation formatting: {e}")
        # Fallback to simple formatting if module fails
        title = source_dict.get('title', 'Unknown Title')
        url = source_dict.get('url', '')
        return f"{title}. Available at: {url}"

# --- Formatting helpers ---
def sanitize_template_for_markdown(template_text: str) -> str:
    """Remove anti-Markdown instructions from prompt templates."""
    if not isinstance(template_text, str):
        return template_text
    cleaned = re.sub(r"(?i)do not use markdown formatting.*", "", template_text)
    return cleaned

def normalize_markdown(text: str) -> str:
    """Ensure lists/paragraphs have proper line breaks across languages."""
    if not isinstance(text, str):
        return ""
    t = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    # Put a newline before each numbered item if multiple items are in one line: "1. a 2. b" -> "1. a\n2. b"
    t = re.sub(r"(?<=\.)\s+(?=\d+\.)", "\n", t)
    # Ensure bullets are on separate lines if jammed: "* a * b" -> "* a\n* b"
    t = re.sub(r"\*\s+(?=\w)", "* ", t)  # normalize bullet spacing
    t = re.sub(r"(?<=\w)\s\*\s", "\n* ", t)
    
    # Ensure tables are on their own lines: "...text | col |" -> "...text\n\n| col |"
    # Matches a sentence ending with punctuation followed by a space and a pipe
    t = re.sub(r"(?<=[\.!?。！？])\s+(\|)", r"\n\n\1", t)
    
    # Collapse excessive blank lines
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t

# Split long analysis into readable paragraphs
def paragraphize_analysis(text: str, max_sentences: int = 5) -> str:
    """Language-agnostic paragraphing for Analysis.
    - Respects existing blank-line breaks
    - Avoids touching lists (bullets/numbered) or tables
    - Groups N sentences per paragraph
    - Strips outer bolding from entire sections
    """
    if not isinstance(text, str):
        return ""
    
    # Strip hallucinated outer bolding (if the whole section is wrapped)
    t = text.strip()
    if len(t) > 200 and t.startswith("**") and t.endswith("**"):
        t = t[2:-2].strip()
        
    t = t.replace("\r\n", "\n").replace("\r", "\n").strip()
    
    # Split by existing blocks
    blocks = t.split("\n\n")
    final_blocks = []
    
    for block in blocks:
        block = block.strip()
        if not block:
            continue
            
        # If block contains list/table markers at start of ANY line, keep as-is
        if re.search(r"^(\*|\d+\.|\s*\||\s*\-\-\-)", block, flags=re.MULTILINE):
            final_blocks.append(block)
            continue
            
        # Already short enough
        sentences = re.split(r"(?<=[\.!?。！？])\s+", block)
        if len(sentences) <= max_sentences:
            final_blocks.append(block)
            continue
            
        # Regroup into smaller paragraphs
        buf = []
        for s in sentences:
            s = s.strip()
            if not s:
                continue
            buf.append(s)
            if len(buf) >= max_sentences:
                final_blocks.append(" ".join(buf))
                buf = []
        if buf:
            final_blocks.append(" ".join(buf))
            
    return "\n\n".join(final_blocks)

# Matches inline bracketed numeric citations like [1], [2], [12]
_CITATION_RE = re.compile(r"\[(\d{1,3})\]")


def _validate_citations(section_text: str, num_sources: int):
    """Ground inline [n] citations against the evidence table size.

    Keeps in-range [n] markers as-is, strips any out-of-range ones, and
    returns (cleaned_text, set_of_valid_cited_ids).
    """
    if not isinstance(section_text, str) or num_sources <= 0:
        return section_text or "", set()

    cited = set()

    def _keep_or_strip(match):
        n = int(match.group(1))
        if 1 <= n <= num_sources:
            cited.add(n)
            return match.group(0)
        return ""

    cleaned = _CITATION_RE.sub(_keep_or_strip, section_text)
    # Tidy whitespace left behind by stripped citations
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    cleaned = re.sub(r" +([.,;:!?)])", r"\1", cleaned)
    return cleaned.strip(), cited


def _uncited_paragraphs(text: str):
    """Return indices of paragraphs containing no inline [n] citation."""
    flagged = []
    paragraphs = [p for p in re.split(r"\n\s*\n", text or "") if p.strip()]
    for idx, para in enumerate(paragraphs):
        if not _CITATION_RE.search(para):
            flagged.append(idx)
    return flagged


# Drafting function with retry logic and deep research support
def draft_answer(
    data: List[Dict[str, Any]], 
    deep_research: bool = False, 
    target_word_count: int = 1000,
    writing_style: str = "academic",
    citation_format: str = "APA",
    language: str = "english",
    retries: int = 3, 
    delay: int = 5
) -> str:
    """Enhanced draft function with style, citations, and language support."""
    if not data:
        return "Error drafting response: No research data provided"

    # Ensure LLM is available before proceeding
    llm_instance = _get_llm()
    if not llm_instance:
        return "Error drafting response: Missing API key. Set OPENROUTER_API_KEY (preferred) or OPENAI_API_KEY with OPENAI_BASE_URL."

    try:
        # Evidence extraction pass: ONE cheap LLM call per topic converts the
        # raw results into a compact evidence table. This replaces injecting
        # json.dumps(data) into every section prompt (up to 6x duplication of
        # all source content in deep mode). Falls back deterministically when
        # no extraction LLM is configured, so drafting is never blocked.
        try:
            evidence = evidence_extractor.extract_evidence(data)
        except Exception as e:
            logging.warning(f"Evidence extraction crashed, using fallback: {type(e).__name__}: {str(e)}")
            evidence = evidence_extractor._fallback_evidence(data)

        # Cost guard: keep the projected input tokens (evidence table x
        # sections + template overhead) under TOKEN_BUDGET_PER_REPORT by
        # dropping lowest-confidence evidence rows first.
        num_sections = 6 if deep_research else 2
        evidence, _budget_info = enforce_token_budget(evidence, data, deep_research, num_sections)
        data_str = evidence_extractor.render_evidence_table(evidence, data)

        # Modify prompts with style and language
        if not deep_research:
            sections = [
                ("Key Findings", apply_writing_style(sanitize_template_for_markdown(key_findings_prompt.template), writing_style)),
                ("Analysis", apply_writing_style(sanitize_template_for_markdown(analysis_prompt.template), writing_style))
            ]
        else:
            sections = [
                ("Abstract", apply_writing_style(sanitize_template_for_markdown(abstract_prompt.template), writing_style)),
                ("Introduction", apply_writing_style(sanitize_template_for_markdown(introduction_prompt.template), writing_style)),
                ("Literature Review", apply_writing_style(sanitize_template_for_markdown(literature_review_prompt.template), writing_style)),
                ("Key Findings", apply_writing_style(sanitize_template_for_markdown(key_findings_prompt.template), writing_style)),
                ("Analysis", apply_writing_style(sanitize_template_for_markdown(analysis_prompt.template), writing_style)),
                ("Conclusion", apply_writing_style(sanitize_template_for_markdown(conclusion_prompt.template), writing_style))
            ]

        # Add language instruction and markdown formatting to system message
        system_message = f"Please provide the response in {language}. "
        system_message += f"Use {citation_format} citation format when referencing sources.\n"
        lang_block = LANGUAGE_PROMPTS.get(language.lower())
        if lang_block:
            system_message += lang_block + "\n"
        system_message += (
            "Cite sources inline using bracketed numbers like [1] or [2] that refer "
            "to the numbered sources in the provided evidence table; every factual "
            "statement should carry a citation. Only cite source numbers that appear "
            "in the table.\n"
            "Format all sections in valid Markdown using appropriate headings and lists. "
            "Do not use HTML; use Markdown constructs only."
        )

        response_text = []
        cited_ids = set()

        # Cost/reliability: model fallback chain. Primary model first, then
        # OPENROUTER_FALLBACK_MODELS. Free OpenRouter endpoints 429 often, so
        # on rate-limit or empty responses we permanently advance to the next
        # model in the chain (once a fallback works, later sections keep it).
        primary_model = os.getenv("OPENROUTER_MODEL") or os.getenv("OPENAI_MODEL") or "unknown"
        model_chain = [primary_model] + [m for m in _get_fallback_models() if m != primary_model]
        current_llm = llm_instance
        current_model_idx = 0

        for section_name, prompt_template in sections:
            messages = [
                {"role": "system", "content": system_message},
                {"role": "user", "content": prompt_template.format(
                    data=data_str,
                    word_count=target_word_count // len(sections)
                )}
            ]

            # Per-section retry: only the failed section is regenerated,
            # already-completed sections are kept as-is.
            last_error = None
            for _section_attempt in range(retries):
                try:
                    response = current_llm.invoke(messages)
                    if not clean_think_tags(response.content.strip()):
                        raise ValueError("empty response from model")
                    break
                except Exception as e:
                    last_error = e
                    if _should_advance_model_chain(e) and current_model_idx < len(model_chain) - 1:
                        next_model = model_chain[current_model_idx + 1]
                        logging.warning(
                            f"Model {model_chain[current_model_idx]} failed for section "
                            f"{section_name} ({type(e).__name__}); advancing to fallback "
                            f"model {next_model}"
                        )
                        current_llm = _llm_for_model(next_model)
                        current_model_idx += 1
                    elif _section_attempt < retries - 1:
                        time.sleep(delay)
            else:
                raise last_error
            section_text = clean_think_tags(response.content.strip())
            
            if section_name == "Key Findings":
                section_text = format_key_findings(section_text)
            # Normalize paragraphs & lists for any language
            section_text = normalize_markdown(section_text)
            if section_name == "Analysis":
                section_text = paragraphize_analysis(section_text, max_sentences=5)

            # Enhancement B: ground inline citations. Strip out-of-range [n]
            # refs, track which source ids were genuinely cited, and flag
            # paragraphs that carry no citation at all.
            num_sources = len(data)
            section_text, sec_cited = _validate_citations(section_text, num_sources)
            if sec_cited:
                cited_ids |= sec_cited
            uncited = _uncited_paragraphs(section_text)
            if uncited:
                logging.warning(
                    f"{section_name}: {len(uncited)} paragraph(s) lack inline [n] citations"
                )

            response_text.append({"title": section_name, "content": section_text})

        # Report the model that actually produced the draft (may be a
        # fallback after rate limits on the primary).
        used_model = model_chain[current_model_idx]
        # References are rendered from the ids ACTUALLY cited inline, so the
        # bibliography never lists a source the report body doesn't support.
        citations = [
            format_citation(data[i - 1], citation_format) for i in sorted(cited_ids)
        ]
        result = {
            "sections": response_text,
            "references": citations,
            "metadata": {
                "model": used_model,
                "language": language,
                "writing_style": writing_style,
                "citation_format": citation_format
            }
        }

        # Tier 3: LLM-as-a-Judge verification pass over the generated
        # sections. Never blocks drafting; appends an explicit per-section
        # verdict summary so readers see what is source-backed.
        try:
            import verification_node
            verifications = verification_node.verify_report(response_text, data, language=language)
            result["verification"] = verifications
            summary_md = verification_node.render_verification_summary(verifications)
            if summary_md:
                response_text.append({
                    "title": "Verification Summary",
                    "content": summary_md
                })
        except Exception as ve:
            logging.warning(f"Verification pass skipped ({type(ve).__name__}: {ve})")

        # Tier 3: contradiction surfacing. Detect conflicting claims across
        # sources and append them explicitly to the report instead of letting
        # the draft silently average them away. Never blocks drafting.
        try:
            import contradiction_detector
            contradictions = contradiction_detector.detect_contradictions(data, language=language)
            result["contradictions"] = contradictions
            block_md = contradiction_detector.render_contradictions_block(contradictions)
            if block_md:
                response_text.append({
                    "title": "Conflicting Evidence",
                    "content": block_md
                })
        except Exception as ce:
            logging.warning(f"Contradiction pass skipped ({type(ce).__name__}: {ce})")

        return json.dumps(result)

    except Exception as e:
        return f"Error drafting response: {type(e).__name__} - {str(e)}"


# Define the tool with support for deep research and target word count using StructuredTool
draft_tool = StructuredTool.from_function(
    func=draft_answer,
    name="DraftAnswer",
    description="Drafts a structured research summary based on research data. Supports deep research mode for detailed summaries and customizable word count.",
    args_schema=DraftAnswerArgs
)
