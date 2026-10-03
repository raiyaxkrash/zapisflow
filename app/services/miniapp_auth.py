"""Telegram HMAC validation. Never use initDataUnsafe as trusted identity."""

import hashlib
import hmac
import json
import re
import time
from urllib.parse import parse_qsl, urlsplit


class MiniAppError(Exception):
    def __init__(self, code: str, message: str, status: int = 400):
        self.code, self.message, self.status = code, message, status


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def validate_init_data(
    raw: str, bot_token: str, *, max_age: int = 300, now: int | None = None
) -> dict:
    """Official bot-token algorithm: HMAC(WebAppData, token), then sorted fields.

    Reject duplicate keys, future timestamps, invalid users and stale credentials.
    New signature field, if present, is part of this HMAC data-check-string;
    only hash is excluded (third-party Ed25519 validation has different rules).
    """
    error = MiniAppError("AUTH_INVALID", "Откройте приложение заново из Telegram", 401)
    if not raw or len(raw) > 16384:
        raise error
    try:
        pairs = parse_qsl(
            raw, keep_blank_values=True, strict_parsing=True, max_num_fields=32
        )
        values = dict(pairs)
        if len(values) != len(pairs):
            raise ValueError
        supplied = values.pop("hash")
        if not re.fullmatch(r"[a-fA-F0-9]{64}", supplied):
            raise ValueError
        check = "\n".join(f"{k}={v}" for k, v in sorted(values.items()))
        secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
        expected = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, supplied.lower()):
            raise ValueError
        auth_date = int(values["auth_date"])
        age = (int(time.time()) if now is None else now) - auth_date
        if age < -30 or age > max_age:
            raise ValueError
        user = json.loads(values["user"])
        if (
            type(user.get("id")) is not int
            or not 0 < user["id"] < 2**53
            or user.get("is_bot")
        ):
            raise ValueError
        if (
            not isinstance(user.get("first_name"), str)
            or not 1 <= len(user["first_name"]) <= 128
        ):
            raise ValueError
        for key, limit in (("last_name", 128), ("username", 64)):
            if key in user and (
                not isinstance(user[key], str) or len(user[key]) > limit
            ):
                raise ValueError
        return user
    except (ValueError, KeyError, TypeError, AttributeError):
        raise error from None


def miniapp_origin(base_url: str) -> str:
    parsed = urlsplit(base_url)
    if (
        parsed.scheme not in {"https", "http"}
        or not parsed.netloc
        or parsed.username
        or parsed.password
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(
            "MINI_APP_BASE_URL must be an origin without path or credentials"
        )
    if parsed.scheme != "https" and parsed.hostname not in {"localhost", "127.0.0.1"}:
        raise ValueError("Mini App requires HTTPS")
    return f"{parsed.scheme}://{parsed.netloc}"
