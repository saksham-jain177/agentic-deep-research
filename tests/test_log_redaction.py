"""Tests for API-key redaction in logs (log_redaction.py)."""

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from log_redaction import (
    SecretRedactionFilter,
    install_redaction_filter,
    redact_secrets,
)

FAKE_OPENAI_KEY = "sk-proj-abc123XYZdef456ghi789"
FAKE_TAVILY_KEY = "tvly-abcdef1234567890abcdef"
FAKE_BEARER_TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.payload.sig"


class ListHandler(logging.Handler):
    """Captures formatted records for assertions."""

    def __init__(self):
        super().__init__()
        self.records = []

    def emit(self, record):
        self.records.append(self.format(record))


def _make_logger():
    logger = logging.getLogger("test_redaction")
    logger.setLevel(logging.INFO)
    handler = ListHandler()
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)
    return logger, handler


class TestRedactSecrets:
    def test_openai_key_redacted(self):
        out = redact_secrets(f"Failed with key {FAKE_OPENAI_KEY} retrying")
        assert FAKE_OPENAI_KEY not in out
        assert "sk-[REDACTED]" in out

    def test_tavily_key_redacted(self):
        out = redact_secrets(f"Using {FAKE_TAVILY_KEY} for search")
        assert FAKE_TAVILY_KEY not in out
        assert "tvly-[REDACTED]" in out

    def test_bearer_token_redacted(self):
        out = redact_secrets(f"Authorization: Bearer {FAKE_BEARER_TOKEN} rejected")
        assert FAKE_BEARER_TOKEN not in out
        assert "Bearer [REDACTED]" in out

    def test_multiple_secrets_in_one_message(self):
        out = redact_secrets(f"{FAKE_OPENAI_KEY} and {FAKE_TAVILY_KEY}")
        assert FAKE_OPENAI_KEY not in out
        assert FAKE_TAVILY_KEY not in out

    def test_normal_text_unchanged(self):
        text = "Research completed: 14 sources, 6 sections, 3.2s elapsed."
        assert redact_secrets(text) == text

    def test_non_string_passthrough(self):
        assert redact_secrets(None) is None

    def test_short_prefix_lookalikes_untouched(self):
        # Too short to be a real key; shouldn't be mangled
        text = "the sk- short tvly- tokens"
        assert redact_secrets(text) == text


class TestSecretRedactionFilter:
    def test_filter_rewrites_record_message(self):
        record = logging.LogRecord(
            name="t", level=logging.INFO, pathname=__file__, lineno=1,
            msg="key=%s", args=(FAKE_OPENAI_KEY,), exc_info=None,
        )
        filt = SecretRedactionFilter()
        assert filt.filter(record) is True  # record kept, not dropped
        assert FAKE_OPENAI_KEY not in record.getMessage()

    def test_filter_through_real_logger(self):
        logger, handler = _make_logger()
        try:
            handler.addFilter(SecretRedactionFilter())
            logger.info("Auth failed for %s", f"Bearer {FAKE_BEARER_TOKEN}")
            assert len(handler.records) == 1
            assert FAKE_BEARER_TOKEN not in handler.records[0]
            assert "Bearer [REDACTED]" in handler.records[0]
        finally:
            logger.removeHandler(handler)

    def test_install_on_root_handlers_is_idempotent(self):
        logger, handler = _make_logger()
        root = logging.getLogger()
        root.addHandler(handler)
        try:
            install_redaction_filter()
            install_redaction_filter()  # second call must not stack duplicates
            matching = [f for f in handler.filters if isinstance(f, SecretRedactionFilter)]
            assert len(matching) == 1
            logger.info(FAKE_TAVILY_KEY)
            assert FAKE_TAVILY_KEY not in handler.records[0]
        finally:
            logger.removeHandler(handler)
            root.removeHandler(handler)
