"""Audit service for safely recording lifecycle and operational events without sensitive data."""

from typing import Any, Dict, Optional
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import TOKEN_REGEX, redact_token
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
    BOT_DELETED = "BOT_DELETED"
    MASTER_ACTIVATED = "MASTER_ACTIVATED"
    PROJECT_SUSPENDED = "PROJECT_SUSPENDED"
    PROJECT_ACTIVATED = "PROJECT_ACTIVATED"
    PLAN_UPDATED = "PLAN_UPDATED"
    TRIAL_CLAIMED = "TRIAL_CLAIMED"
    TRIAL_REJECTED_ALREADY_USED = "TRIAL_REJECTED_ALREADY_USED"


class AuditService:
    """Persists sanitized audit log events."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    @classmethod
    def _sanitize_value(cls, val: Any) -> Any:
        if isinstance(val, dict):
            return cls._sanitize_dict(val)
        if isinstance(val, (list, tuple, set)):
            sanitized_list = [cls._sanitize_value(item) for item in val]
            return type(val)(sanitized_list) if not isinstance(val, set) else set(sanitized_list)
        if isinstance(val, str):
            if val.startswith("v1:"):
                return "[REDACTED]"
            if val.startswith("whsec_"):
                return "[REDACTED]"
            if TOKEN_REGEX.search(val):
                return "[REDACTED]"
            return redact_token(val)
        return val

    @classmethod
    def _sanitize_dict(cls, payload: Dict[str, Any]) -> Dict[str, Any]:
        sensitive_key_terms = ("token", "secret", "password", "key", "cipher", "credential", "auth")
        result = {}
        for k, v in payload.items():
            k_lower = str(k).lower()
            if any(term in k_lower for term in sensitive_key_terms):
                result[k] = "[REDACTED]"
            else:
                result[k] = cls._sanitize_value(v)
        return result

    @classmethod
    def _sanitize_payload(cls, payload: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """Scrub tokens, secrets, ciphertexts and sensitive fields recursively."""
        if not payload:
            return None
        return cls._sanitize_dict(payload)

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
