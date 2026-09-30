"""Security utilities for log redaction, token masking, and safe formatting."""

import logging
import re
from typing import Any

# Telegram bot token pattern: <bot_id>:<secret_35_chars>
TOKEN_REGEX = re.compile(r"\b(\d{8,10}):([A-Za-z0-9_-]{35})\b")
TELEGRAM_API_URL_REGEX = re.compile(r"(api\.telegram\.org/bot)(\d{8,10}:[A-Za-z0-9_-]{35})")


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
        if isinstance(record.msg, str):
            record.msg = redact_token(record.msg)

        if record.args:
            if isinstance(record.args, dict):
                record.args = {k: redact_token(v) if isinstance(v, str) else v for k, v in record.args.items()}
            elif isinstance(record.args, (list, tuple)):
                record.args = tuple(redact_token(v) if isinstance(v, str) else v for v in record.args)

        if record.exc_text:
            record.exc_text = redact_token(record.exc_text)

        return True
