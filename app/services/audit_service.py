"""Audit service for safely recording lifecycle and operational events without sensitive data."""

from typing import Any, Dict, Optional
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models.audit import AuditLog


class AuditEvent:
    """Canonical audit action types."""

    MASTER_CREATED = "MASTER_CREATED"
    BOT_PROVISION_STARTED = "BOT_PROVISION_STARTED"
    BOT_CONNECTED = "BOT_CONNECTED"
    BOT_PROVISION_FAILED = "BOT_PROVISION_FAILED"
    BOT_TOKEN_ROTATED = "BOT_TOKEN_ROTATED"
    BOT_DISABLED = "BOT_DISABLED"
    BOT_ENABLED = "BOT_ENABLED"
    MASTER_ACTIVATED = "MASTER_ACTIVATED"


class AuditService:
    """Persists sanitized audit log events."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    @staticmethod
    def _sanitize_payload(payload: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """Scrub tokens, secrets and ciphertexts from audit dictionaries."""
        if not payload:
            return None
        sensitive_keys = {
            "token",
            "raw_token",
            "bot_token",
            "encrypted_token",
            "ciphertext",
            "webhook_secret",
            "secret_token",
            "password",
            "master_key",
            "key",
        }
        return {
            k: "[REDACTED]" if k.lower() in sensitive_keys else v
            for k, v in payload.items()
        }

    async def log_event(
        self,
        action: str,
        actor_user_id: Optional[int] = None,
        master_id: Optional[int] = None,
        entity_type: str = "BotInstance",
        entity_id: Optional[int] = None,
        payload_before: Optional[Dict[str, Any]] = None,
        payload_after: Optional[Dict[str, Any]] = None,
    ) -> AuditLog:
        """Create and flush an audit log entry."""
        clean_before = self._sanitize_payload(payload_before)
        clean_after = self._sanitize_payload(payload_after)

        record = AuditLog(
            action=action,
            actor_user_id=actor_user_id,
            master_id=master_id,
            entity_type=entity_type,
            entity_id=entity_id,
            payload_before=clean_before,
            payload_after=clean_after,
        )
        self.session.add(record)
        await self.session.flush()
        return record
