"""Tests for fail-fast PostgreSQL connectivity and extension verification."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.exc import OperationalError

from app.database import session as session_module


@pytest.mark.asyncio
async def test_init_db_fails_fast_when_database_is_unreachable() -> None:
    """When PostgreSQL is down, init_db() must loudly raise RuntimeError without fallback."""
    mock_engine = MagicMock()
    mock_engine.connect.side_effect = OperationalError("connection refused", {}, None)

    with patch.object(session_module, "engine", mock_engine):
        with pytest.raises(RuntimeError, match="Failed to connect to PostgreSQL"):
            await session_module.init_db()


@pytest.mark.asyncio
async def test_init_db_fails_when_btree_gist_is_missing() -> None:
    """When btree_gist extension is missing, init_db() must raise RuntimeError."""
    mock_conn = AsyncMock()
    # First query is SELECT 1, second query checks pg_extension for btree_gist
    mock_cursor = MagicMock()
    mock_cursor.scalar.return_value = None  # btree_gist not found
    mock_conn.execute.side_effect = [MagicMock(), mock_cursor]

    mock_connect_ctx = AsyncMock()
    mock_connect_ctx.__aenter__.return_value = mock_conn
    mock_connect_ctx.__aexit__.return_value = None

    mock_engine = MagicMock()
    mock_engine.connect.return_value = mock_connect_ctx

    with patch.object(session_module, "engine", mock_engine):
        with pytest.raises(RuntimeError, match="btree_gist"):
            await session_module.init_db()


@pytest.mark.asyncio
async def test_init_db_succeeds_when_postgres_and_extension_ready() -> None:
    """When PostgreSQL and btree_gist are present, init_db() succeeds."""
    mock_conn = AsyncMock()
    mock_cursor = MagicMock()
    mock_cursor.scalar.return_value = 1  # btree_gist found
    mock_conn.execute.side_effect = [MagicMock(), mock_cursor]

    mock_connect_ctx = AsyncMock()
    mock_connect_ctx.__aenter__.return_value = mock_conn
    mock_connect_ctx.__aexit__.return_value = None

    mock_engine = MagicMock()
    mock_engine.connect.return_value = mock_connect_ctx

    with patch.object(session_module, "engine", mock_engine):
        await session_module.init_db()
        assert mock_conn.execute.await_count == 2
