"""Guard the production Docker configuration against secret and port leaks."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _service(compose: str, name: str) -> str:
    match = re.search(
        rf"(?ms)^  {re.escape(name)}:\s*\n(.*?)(?=^  [\w-]+:|^\S|\Z)",
        compose,
    )
    assert match is not None, f"Missing {name} service"
    return match.group(1)


def test_dockerignore_excludes_secret_env_files() -> None:
    patterns = {
        line.strip()
        for line in (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }

    assert ".env" in patterns
    assert ".env.*" in patterns
    assert "!.env.example" in patterns
    assert ".git" in patterns or ".git/" in patterns
    assert ".venv" in patterns or ".venv/" in patterns
    assert "*.sqlite3" in patterns
    assert "backups/" in patterns or "*.bak" in patterns


def test_compose_does_not_contain_hardcoded_passwords() -> None:
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert "postgres_secure_password" not in compose
    assert "${POSTGRES_PASSWORD}" in compose
    assert "${DATABASE_URL}" in compose


def test_postgres_database_uses_named_volume_and_services_are_private() -> None:
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    postgres = _service(compose, "postgres")
    redis = _service(compose, "redis")
    migrate = _service(compose, "migrate")
    bot = _service(compose, "bot")

    assert "image: postgres:16-alpine" in postgres
    assert "pg_isready" in postgres
    assert not re.search(r"(?m)^    ports:\s*$", postgres)
    assert not re.search(r"(?m)^    ports:\s*$", redis)
    assert re.search(r"(?m)^      DATABASE_URL:\s*.+$", migrate)
    assert re.search(r"(?m)^      DATABASE_URL:\s*.+$", bot)
    assert re.search(r"(?m)^      - postgres_data:/var/lib/postgresql/data\s*$", postgres)
    assert re.search(r"(?m)^  postgres_data:\s*$", compose)


def test_bot_uses_internal_services_without_project_bind_mount() -> None:
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    bot = _service(compose, "bot")

    assert re.search(r"(?m)^      DATABASE_URL:\s*.+$", bot)
    assert re.search(r"(?m)^      REDIS_HOST:\s*redis\s*$", bot)
    assert not re.search(r"(?m)^\s*-\s*\.:/app\s*$", bot)
