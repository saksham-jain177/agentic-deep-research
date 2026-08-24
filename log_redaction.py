"""
Secret redaction for application logs.

research_agent.log is written at INFO level by several modules (draft_agent,
evidence_extractor, app). Exception strings and debug messages can carry API
keys or Authorization headers; this module installs a logging.Filter that
rewrites anything shaped like a secret before it hits disk:

* OpenAI-style keys:  sk-...
* Tavily keys:        tvly-...
* Authorization headers: Bearer <token>

The filter mutates the record in place (record.msg/args) so every handler
attached downstream sees the redacted message.
"""

import logging
import re
from typing import List, Tuple

SECRET_PATTERNS: List[Tuple[re.Pattern, str]] = [
    # sk-... (OpenAI / OpenRouter style keys)
    (re.compile(r"\bsk-[A-Za-z0-9_-]{6,}\b"), "sk-[REDACTED]"),
    # tvly-... (Tavily keys)
    (re.compile(r"\btvly-[A-Za-z0-9_-]{6,}\b"), "tvly-[REDACTED]"),
    # Bearer tokens (Authorization headers echoed in HTTP errors)
    (
        re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{8,}={0,2}", re.IGNORECASE),
        "Bearer [REDACTED]",
    ),
]


def redact_secrets(text: str) -> str:
    """Replace key-shaped substrings with [REDACTED] markers."""
    if not isinstance(text, str):
        return text
    for pattern, replacement in SECRET_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


class SecretRedactionFilter(logging.Filter):
    """Logging filter that redacts secrets from every emitted record."""

    def filter(self, record: logging.LogRecord) -> bool:
        original = record.getMessage()
        redacted = redact_secrets(original)
        if redacted != original:
            record.msg = redacted
            record.args = None  # already interpolated above
        return True  # never drop the record


def install_redaction_filter(logger: logging.Logger = None) -> SecretRedactionFilter:
    """Attach the redaction filter to a logger's handlers (root by default).

    Idempotent per handler: skips handlers that already carry the filter so
    repeated basicConfig+install calls don't stack duplicates.
    """
    target = logger if logger is not None else logging.getLogger()
    filt = SecretRedactionFilter()
    handlers = target.handlers if target.handlers else []
    for handler in handlers:
        if not any(isinstance(f, SecretRedactionFilter) for f in handler.filters):
            handler.addFilter(filt)
    return filt
