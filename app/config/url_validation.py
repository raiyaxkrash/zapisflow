"""Dependency-light URL validation shared by configuration and adapters."""

from urllib.parse import urlsplit
from ipaddress import ip_address


def webhook_origin(base_url: str, *, production: bool = False) -> str:
    """Validate production webhook origin without disclosing supplied credentials."""
    if production:
        try:
            parsed = urlsplit(base_url)
            if (parsed.scheme != "https" or not parsed.hostname or parsed.username
                    or parsed.password or parsed.path not in {"", "/"}
                    or parsed.query or parsed.fragment or parsed.hostname == "localhost"
                    or "%" in parsed.netloc or "." not in parsed.hostname
                    or not any(c.isalpha() for c in parsed.hostname)):
                raise ValueError
            parsed.port  # Reject malformed ports as configuration errors.
            try:
                ip_address(parsed.hostname)
            except ValueError:
                pass
            else:
                raise ValueError
        except ValueError as exc:
            raise ValueError("WEBHOOK_BASE_URL must be an HTTPS hostname origin without credentials, path, query or fragment") from exc
    return base_url.rstrip("/")


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
