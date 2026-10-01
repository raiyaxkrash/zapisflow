"""Production startup must fail closed when prerequisites are missing."""

import json
import re
from pathlib import Path

import pytest

import app.main as application


ROOT = Path(__file__).resolve().parents[1]
VALID_BOT_TOKEN = "123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrs"


def _compose_service(name: str) -> str:
    """Read one top-level Compose service without requiring a YAML test dependency."""
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    match = re.search(
        rf"(?ms)^  {re.escape(name)}:\s*\n(.*?)(?=^  [\w-]+:|^volumes:|\Z)",
        compose,
    )
    assert match is not None, f"Compose service {name!r} is missing"
    return match.group(1)


def _service_command(service: str) -> str:
    match = re.search(r"(?m)^    command:\s*(.+)$", service)
    assert match is not None, "Compose service command is missing"
    command = match.group(1).strip()
    return " ".join(json.loads(command)) if command.startswith("[") else command


@pytest.mark.asyncio
async def test_main_fails_if_database_is_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(application.settings, "bot_token", VALID_BOT_TOKEN)

    async def unavailable_database() -> None:
        raise ConnectionError("database unavailable")

    def must_not_start_bot():
        pytest.fail("Bot must not start after a failed database check")

    monkeypatch.setattr(application, "init_db", unavailable_database, raising=False)
    monkeypatch.setattr(application, "create_bot", must_not_start_bot)

    with pytest.raises(ConnectionError, match="database unavailable"):
        await application.main()


@pytest.mark.asyncio
async def test_main_rejects_placeholder_token_before_database_use(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(application.settings, "bot_token", "dummy_token_for_init")

    async def must_not_open_database() -> None:
        pytest.fail("Invalid token must fail before checking the database")

    monkeypatch.setattr(application, "init_db", must_not_open_database, raising=False)

    with pytest.raises(RuntimeError, match="BOT_TOKEN"):
        await application.main()


def test_compose_runs_migrations_as_a_separate_completed_service() -> None:
    migration = _compose_service("migrate")
    backend = _compose_service("backend")

    assert re.search(r"\balembic\s+upgrade\s+head\b", _service_command(migration))
    assert re.search(
        r"(?m)^      migrate:\s*\n        condition: service_completed_successfully\s*$",
        backend,
    )
    assert "alembic upgrade" not in _service_command(backend)
    assert re.search(r"\bpython\s+-m\s+app\.main\b", _service_command(backend))
