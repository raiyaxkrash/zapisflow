"""Dependency-light URL validation shared by configuration and adapters."""

from urllib.parse import urlsplit


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
