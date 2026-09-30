"""Bot provisioning service coordinating onboarding, validation, encryption, webhook setup, and token rotation."""

import logging
import secrets
from typing import List, Optional, Tuple
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import settings
from app.core.token_crypto import TokenCrypto
from app.database.models.master import BotInstance, BotInstanceStatus, Master, MasterStatus
from app.repositories.bot_instance_repository import BotInstanceRepository
from app.repositories.master_repository import MasterRepository
from app.services.audit_service import AuditEvent, AuditService
from app.services.bot_registry import BotRegistry
from app.services.exceptions import (
    AccessDeniedError,
    DuplicateBotError,
    ManagerTokenCollisionError,
    ProvisioningWebhookError,
    TelegramGatewayError,
    TokenRotationBotMismatchError,
)
from app.services.master_readiness_service import MasterReadinessService
from app.services.telegram_provisioning_gateway import BotIdentity, TelegramProvisioningGateway

logger = logging.getLogger(__name__)


class BotProvisioningService:
    """Coordinates tenant bot onboarding and credential lifecycle."""

    def __init__(
        self,
        session: AsyncSession,
        gateway: Optional[TelegramProvisioningGateway] = None,
        crypto: Optional[TokenCrypto] = None,
        registry: Optional[BotRegistry] = None,
    ) -> None:
        self.session = session
        self.gateway = gateway or TelegramProvisioningGateway()
        self.crypto = crypto or TokenCrypto()
        self.registry = registry
        self.bot_repo = BotInstanceRepository(session)
        self.master_repo = MasterRepository(session)
        self.audit_service = AuditService(session)
        self.readiness_service = MasterReadinessService(session)

    async def validate_candidate_token(
        self,
        actor_user_id: int,
        token: str,
        manager_bot_id: Optional[int] = None,
    ) -> BotIdentity:
        """Validate candidate token with Telegram getMe and ensure uniqueness."""
        clean_token = token.strip()

        # 1. Prevent collision with platform Manager Bot token
        if settings.manager_bot_token and clean_token == settings.manager_bot_token.strip():
            raise ManagerTokenCollisionError("Запрещено подключать токен управляющего бота платформы.")

        # 2. Call Telegram getMe
        identity = await self.gateway.validate_token(clean_token)

        # 3. Check collision with manager bot Telegram ID
        if manager_bot_id is not None and identity.id == manager_bot_id:
            raise ManagerTokenCollisionError("Запрещено подключать токен управляющего бота платформы.")

        # 4. Check global uniqueness of telegram_bot_id
        existing = await self.bot_repo.get_by_telegram_bot_id(identity.id)
        if existing and existing.status in (
            BotInstanceStatus.ACTIVE,
            BotInstanceStatus.SETUP_REQUIRED,
            BotInstanceStatus.PROVISIONING,
        ):
            # Generic friendly message without leaking owner or tenant details
            raise DuplicateBotError("Этот Telegram-бот уже подключён к платформе.")

        return identity

    async def provision_bot(
        self,
        master_id: int,
        actor_user_id: int,
        token: str,
        bot_identity: BotIdentity,
    ) -> BotInstance:
        """Execute full onboarding flow: encrypt -> DB COMMIT -> setWebhook -> status update."""
        clean_token = token.strip()

        # 1. Verify Master ownership
        master = await self.master_repo.get_by_id(master_id)
        if not master or master.owner_user_id != actor_user_id:
            raise AccessDeniedError("У вас нет прав на управление данным проектом.")

        # 2. Encrypt token via AES-256-GCM with telegram_bot_id as associated data
        encrypted_token = self.crypto.encrypt(clean_token, associated_data=bot_identity.id)
        webhook_secret = secrets.token_urlsafe(32)

        # 3. Create BotInstance in PROVISIONING state
        bot_instance = await self.bot_repo.create_bot_instance(
            master_id=master_id,
            telegram_bot_id=bot_identity.id,
            telegram_username=bot_identity.username,
            telegram_first_name=bot_identity.first_name,
            encrypted_token=encrypted_token,
            webhook_secret=webhook_secret,
            status=BotInstanceStatus.PROVISIONING,
            token_version=1,
        )

        # 4. DB COMMIT BEFORE Network call (Transaction boundary separation)
        await self.session.commit()

        await self.audit_service.log_event(
            action=AuditEvent.BOT_PROVISION_STARTED,
            actor_user_id=actor_user_id,
            master_id=master_id,
            entity_id=bot_instance.id,
            payload_after={"telegram_bot_id": bot_identity.id, "status": "PROVISIONING"},
        )
        await self.session.commit()

        # 5. Build webhook URL using public_id (never exposing token or secret in URL)
        base_url = settings.webhook_base_url.rstrip("/")
        webhook_url = f"{base_url}/telegram/webhook/{bot_instance.public_id}"

        # 6. Execute network setWebhook
        try:
            await self.gateway.set_webhook(
                token=clean_token,
                url=webhook_url,
                secret_token=webhook_secret,
                drop_pending_updates=False,
            )
            # Success -> advance status to SETUP_REQUIRED
            bot_instance.status = BotInstanceStatus.SETUP_REQUIRED
            bot_instance.last_error = None
            await self.session.commit()

            await self.audit_service.log_event(
                action=AuditEvent.BOT_CONNECTED,
                actor_user_id=actor_user_id,
                master_id=master_id,
                entity_id=bot_instance.id,
                payload_after={"status": "SETUP_REQUIRED"},
            )
            await self.session.commit()
            return bot_instance
        except Exception as exc:
            # Failure -> mark ERROR status with safe description
            error_desc = f"Ошибка подключения вебхука: {str(exc)[:200]}"
            bot_instance.status = BotInstanceStatus.ERROR
            bot_instance.last_error = error_desc
            await self.session.commit()

            await self.audit_service.log_event(
                action=AuditEvent.BOT_PROVISION_FAILED,
                actor_user_id=actor_user_id,
                master_id=master_id,
                entity_id=bot_instance.id,
                payload_after={"status": "ERROR", "error": error_desc},
            )
            await self.session.commit()
            raise ProvisioningWebhookError(error_desc) from exc

    async def retry_provisioning(
        self,
        bot_instance_id: int,
        actor_user_id: int,
    ) -> BotInstance:
        """Retry setWebhook for an ERROR BotInstance without creating duplicates."""
        bot_instance = await self.bot_repo.get_by_id_and_owner(bot_instance_id, actor_user_id)
        if not bot_instance:
            raise AccessDeniedError("Экземпляр бота не найден или доступ запрещен.")

        if not bot_instance.encrypted_token or not bot_instance.telegram_bot_id:
            raise ProvisioningWebhookError("Токен бота не настроен.")

        # Decrypt token
        raw_token = self.crypto.decrypt(
            bot_instance.encrypted_token,
            associated_data=bot_instance.telegram_bot_id,
        )

        base_url = settings.webhook_base_url.rstrip("/")
        webhook_url = f"{base_url}/telegram/webhook/{bot_instance.public_id}"

        try:
            await self.gateway.set_webhook(
                token=raw_token,
                url=webhook_url,
                secret_token=bot_instance.webhook_secret,
                drop_pending_updates=False,
            )
            bot_instance.status = BotInstanceStatus.SETUP_REQUIRED
            bot_instance.last_error = None
            await self.session.commit()

            await self.audit_service.log_event(
                action=AuditEvent.BOT_CONNECTED,
                actor_user_id=actor_user_id,
                master_id=bot_instance.master_id,
                entity_id=bot_instance.id,
                payload_after={"status": "SETUP_REQUIRED", "retry": True},
            )
            await self.session.commit()
            return bot_instance
        except Exception as exc:
            error_desc = f"Ошибка повторного подключения: {str(exc)[:200]}"
            bot_instance.status = BotInstanceStatus.ERROR
            bot_instance.last_error = error_desc
            await self.session.commit()
            raise ProvisioningWebhookError(error_desc) from exc

    async def rotate_token(
        self,
        bot_instance_id: int,
        actor_user_id: int,
        new_token: str,
    ) -> BotInstance:
        """Rotate token for an existing bot instance, enforcing matching telegram_bot_id."""
        clean_token = new_token.strip()
        bot_instance = await self.bot_repo.get_by_id_and_owner(bot_instance_id, actor_user_id)
        if not bot_instance:
            raise AccessDeniedError("Экземпляр бота не найден или доступ запрещен.")

        # 1. Validate getMe for new token
        identity = await self.gateway.validate_token(clean_token)

        # 2. Strict ID matching: new token must belong to the exact same Telegram bot
        if identity.id != bot_instance.telegram_bot_id:
            raise TokenRotationBotMismatchError("Это токен другого Telegram-бота.")

        # 3. Encrypt new token
        new_encrypted = self.crypto.encrypt(clean_token, associated_data=identity.id)

        # 4. Advance version and save ciphertext
        bot_instance.encrypted_token = new_encrypted
        bot_instance.token_version += 1
        bot_instance.telegram_username = identity.username
        bot_instance.telegram_first_name = identity.first_name
        await self.session.commit()

        # 5. Update webhook with new token
        base_url = settings.webhook_base_url.rstrip("/")
        webhook_url = f"{base_url}/telegram/webhook/{bot_instance.public_id}"

        try:
            await self.gateway.set_webhook(
                token=clean_token,
                url=webhook_url,
                secret_token=bot_instance.webhook_secret,
            )
        except Exception as exc:
            bot_instance.status = BotInstanceStatus.ERROR
            bot_instance.last_error = f"Ошибка вебхука при ротации: {str(exc)[:200]}"
            await self.session.commit()
            raise ProvisioningWebhookError("Токен сохранен, но вебхук не удалось обновить.") from exc

        # 6. Invalidate BotRegistry runtime pool and Redis bus
        if self.registry:
            await self.registry.invalidate_bot(bot_instance.id)

        await self.audit_service.log_event(
            action=AuditEvent.BOT_TOKEN_ROTATED,
            actor_user_id=actor_user_id,
            master_id=bot_instance.master_id,
            entity_id=bot_instance.id,
            payload_after={"token_version": bot_instance.token_version},
        )
        await self.session.commit()
        return bot_instance

    async def disable_bot(
        self,
        bot_instance_id: int,
        actor_user_id: int,
    ) -> BotInstance:
        """Disable a customer bot and remove its webhook."""
        bot_instance = await self.bot_repo.get_by_id_and_owner(bot_instance_id, actor_user_id)
        if not bot_instance:
            raise AccessDeniedError("Экземпляр бота не найден или доступ запрещен.")

        # 1. Best-effort deleteWebhook
        if bot_instance.encrypted_token and bot_instance.telegram_bot_id:
            try:
                raw_token = self.crypto.decrypt(
                    bot_instance.encrypted_token,
                    associated_data=bot_instance.telegram_bot_id,
                )
                await self.gateway.delete_webhook(raw_token)
            except Exception as exc:
                logger.warning(
                    "deleteWebhook failed during disable for bot #%s (continuing): %s",
                    bot_instance.id,
                    exc,
                )

        # 2. Update status in database
        bot_instance.status = BotInstanceStatus.DISABLED
        await self.session.commit()

        # 3. Invalidate BotRegistry
        if self.registry:
            await self.registry.invalidate_bot(bot_instance.id)

        await self.audit_service.log_event(
            action=AuditEvent.BOT_DISABLED,
            actor_user_id=actor_user_id,
            master_id=bot_instance.master_id,
            entity_id=bot_instance.id,
            payload_after={"status": "DISABLED"},
        )
        await self.session.commit()
        return bot_instance

    async def enable_bot(
        self,
        bot_instance_id: int,
        actor_user_id: int,
    ) -> BotInstance:
        """Re-enable a disabled bot by restoring its webhook."""
        bot_instance = await self.bot_repo.get_by_id_and_owner(bot_instance_id, actor_user_id)
        if not bot_instance:
            raise AccessDeniedError("Экземпляр бота не найден или доступ запрещен.")

        if not bot_instance.encrypted_token or not bot_instance.telegram_bot_id:
            raise ProvisioningWebhookError("Токен бота не настроен.")

        raw_token = self.crypto.decrypt(
            bot_instance.encrypted_token,
            associated_data=bot_instance.telegram_bot_id,
        )

        # Re-validate with getMe
        await self.gateway.validate_token(raw_token)

        # Re-install webhook
        base_url = settings.webhook_base_url.rstrip("/")
        webhook_url = f"{base_url}/telegram/webhook/{bot_instance.public_id}"

        await self.gateway.set_webhook(
            token=raw_token,
            url=webhook_url,
            secret_token=bot_instance.webhook_secret,
        )

        bot_instance.status = BotInstanceStatus.SETUP_REQUIRED
        bot_instance.last_error = None
        await self.session.commit()

        await self.audit_service.log_event(
            action=AuditEvent.BOT_ENABLED,
            actor_user_id=actor_user_id,
            master_id=bot_instance.master_id,
            entity_id=bot_instance.id,
            payload_after={"status": "SETUP_REQUIRED"},
        )
        await self.session.commit()
        return bot_instance

    async def activate_master_and_bot(
        self,
        master_id: int,
        actor_user_id: int,
    ) -> Tuple[bool, List[str]]:
        """Validate onboarding checklist and activate Master and BotInstance."""
        master = await self.master_repo.get_by_id(master_id)
        if not master or master.owner_user_id != actor_user_id:
            raise AccessDeniedError("У вас нет прав на управление данным проектом.")

        is_ready, missing = await self.readiness_service.check(master_id)
        if not is_ready:
            return False, missing

        # Readiness passed -> activate
        master.status = MasterStatus.ACTIVE
        bot_instance = await self.bot_repo.get_current_for_master(master_id)
        if bot_instance and bot_instance.status == BotInstanceStatus.SETUP_REQUIRED:
            bot_instance.status = BotInstanceStatus.ACTIVE

        await self.session.commit()

        await self.audit_service.log_event(
            action=AuditEvent.MASTER_ACTIVATED,
            actor_user_id=actor_user_id,
            master_id=master_id,
            entity_id=bot_instance.id if bot_instance else None,
            payload_after={"master_status": "ACTIVE", "bot_status": "ACTIVE"},
        )
        await self.session.commit()
        return True, []
