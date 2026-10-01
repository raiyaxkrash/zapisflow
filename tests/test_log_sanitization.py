"""Ensure secret redaction covers exception tracebacks on every logging handler."""

import io
import logging

from app.core.security import install_sensitive_logging


def test_token_is_redacted_from_message_and_traceback_without_handler_filter() -> None:
    original_factory = logging.getLogRecordFactory()
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger = logging.getLogger("tests.security.secret_traceback")
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    fake_token = "123456789:" + "A" * 35

    try:
        install_sensitive_logging()
        logger.error("Request to https://api.telegram.org/bot%s/sendMessage failed", fake_token)
        try:
            raise ValueError(f"Telegram rejected {fake_token}")
        except ValueError:
            logger.exception("Bot call failed")
    finally:
        logger.removeHandler(handler)
        logging.setLogRecordFactory(original_factory)

    output = stream.getvalue()
    assert fake_token not in output
    assert "[REDACTED_BOT_TOKEN]" in output
    assert "[REDACTED_SECRET]" in output
