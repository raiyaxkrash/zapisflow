"""Security utilities for log redaction, token masking, and safe formatting."""

import logging
import re
from typing import Any

# Telegram bot token pattern: <bot_id>:<secret> (future-proofed for 5-16 digits and 30-50 char secrets)
TOKEN_REGEX = re.compile(r"\b(\d{5,16}):([A-Za-z0-9_-]{30,50})\b")
TELEGRAM_API_URL_REGEX = re.compile(r"(api\.telegram\.org/bot)(\d{5,16}:[A-Za-z0-9_-]{30,50})")


def redact_token(text: str) -> str:
    """Scrub raw Telegram bot tokens and API URLs from log messages and exception strings."""
    if not isinstance(text, str):
        return text

    # Redact full token in URL
    text = TELEGRAM_API_URL_REGEX.sub(r"\1[REDACTED_BOT_TOKEN]", text)
    # Redact standalone tokens
    text = TOKEN_REGEX.sub(r"\1:[REDACTED_SECRET]", text)
    return text


def mask_token(token: str | None = None) -> str:
    """Return a safe preview of a bot token (e.g. '123456789:***') for diagnostic logs."""
    if not token or not isinstance(token, str):
        return "[EMPTY]"
    parts = token.split(":", 1)
    if len(parts) == 2:
        return f"{parts[0]}:***"
    return "***"


class SensitiveDataFilter(logging.Filter):
    """Logging filter that redacts Telegram bot tokens and secrets from all log records."""

    def filter(self, record: logging.LogRecord) -> bool:
        sanitize_log_record(record)
        return True


def sanitize_log_record(record: logging.LogRecord) -> logging.LogRecord:
    """Redact the rendered message and traceback before any handler formats them."""
    try:
        record.msg = redact_token(record.getMessage())
        record.args = ()
    except Exception:
        if isinstance(record.msg, str):
            record.msg = redact_token(record.msg)

    if record.exc_info:
        # Formatter normally builds exc_text after filters have run. Precompute the
        # sanitized traceback so every handler uses the safe cached value.
        record.exc_text = redact_token(logging.Formatter().formatException(record.exc_info))
        record.exc_info = None
    elif record.exc_text:
        record.exc_text = redact_token(record.exc_text)

    if record.stack_info:
        record.stack_info = redact_token(record.stack_info)
    return record


def install_sensitive_logging() -> None:
    """Cover all handlers, including those installed later by Uvicorn."""
    current_factory = logging.getLogRecordFactory()
    if getattr(current_factory, "_zapisflow_sensitive_factory", False):
        return

    def safe_factory(*args: Any, **kwargs: Any) -> logging.LogRecord:
        return sanitize_log_record(current_factory(*args, **kwargs))

    safe_factory._zapisflow_sensitive_factory = True  # type: ignore[attr-defined]
    logging.setLogRecordFactory(safe_factory)
