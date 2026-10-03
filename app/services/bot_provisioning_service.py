"""Bot provisioning service coordinating onboarding, validation, encryption, webhook setup, and token rotation."""

import logging
import secrets
from typing import List, Optional, Tuple
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import settings
from app.core.security import redact_token
from app.core.token_crypto import TokenCrypto
from app.database.models.master import BotInstance, BotInstanceStatus, Master, MasterStatus
from app.repositories.bot_instance_repository import BotInstanceRepository
from app.repositories.master_repository import MasterRepository
from app.services.audit_service import AuditEvent, AuditService
from app.services.bot_registry import BotRegistry
from app.services.exceptions import (
    AccessDeniedError,
    DuplicateBotError,
    InvalidBotTokenError,
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
        if not clean_token or ":" not in clean_token:
            raise InvalidBotTokenError("Некорректный синтаксис токена Telegram бота.")
        prefix, _, _ = clean_token.partition(":")
        if not prefix.isdigit():
            raise InvalidBotTokenError("Некорректный синтаксис токена Telegram бота.")

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
        # Serialize connect attempts for one project. This lock is held until the
        # durable PROVISIONING row is committed below.
        master = await self.session.scalar(
            select(Master).where(Master.id == master_id).with_for_update()
        )
        if not master or master.owner_user_id != actor_user_id:
            raise AccessDeniedError("У вас нет прав на управление данным проектом.")

        # 2. Check current bot instance for this master
        existing_current = await self.bot_repo.get_current_for_master(master_id)
        if existing_current and existing_current.telegram_bot_id == bot_identity.id:
            current_token = (
                self.crypto.decrypt(
                    existing_current.encrypted_token,
                    associated_data=bot_identity.id,
                )
                if existing_current.encrypted_token else None
            )
            if current_token and secrets.compare_digest(current_token, clean_token):
                # The first attempt may have committed before its webhook update
                # completed. Resume that durable step without creating another
                # BotInstance, token version, or trial.
                if existing_current.status in (BotInstanceStatus.PROVISIONING, BotInstanceStatus.ERROR):
                    return await self.retry_provisioning(existing_current.id, actor_user_id)
                if existing_current.status in (BotInstanceStatus.SETUP_REQUIRED, BotInstanceStatus.ACTIVE):
                    return existing_current
        if existing_current and existing_current.status in (
            BotInstanceStatus.ACTIVE,
            BotInstanceStatus.PROVISIONING,
        ):
            raise DuplicateBotError("У этого проекта уже есть подключённый активный бот.")

        # Deprecate previous current bot if exists
        if existing_current:
            await self.bot_repo.deprecate_current_for_master(master_id)

        # 3. Encrypt token via AES-256-GCM with telegram_bot_id as associated data
        encrypted_token = self.crypto.encrypt(clean_token, associated_data=bot_identity.id)
        webhook_secret = secrets.token_urlsafe(32)

        # 4. Create BotInstance in PROVISIONING state and commit BEFORE network call
        try:
            bot_instance = await self.bot_repo.create_bot_instance(
                master_id=master_id,
                telegram_bot_id=bot_identity.id,
                telegram_username=bot_identity.username,
                telegram_first_name=bot_identity.first_name,
                encrypted_token=encrypted_token,
                webhook_secret=webhook_secret,
                status=BotInstanceStatus.PROVISIONING,
                token_version=1,
                is_current=True,
            )
            await self.session.commit()
        except IntegrityError as exc:
            await self.session.rollback()
            orig_str = str(exc.orig) if hasattr(exc, "orig") else str(exc)
            if "telegram_bot_id" in orig_str:
                raise DuplicateBotError("Этот Telegram-бот уже подключён к платформе.") from exc
            if "current_per_master" in orig_str:
                raise DuplicateBotError("Для данного проекта уже выполняется подключение бота.") from exc
            raise DuplicateBotError("Конфликт при подключении бота.") from exc

        if existing_current and self.registry:
            await self.registry.invalidate_bot(existing_current.id, reason="bot_reconnected")

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

            # 7. Verification of webhook info (Section 14)
            info = await self.gateway.get_webhook_info(clean_token)
            if clean_token in (info.url or "") or (webhook_secret and webhook_secret in (info.url or "")):
                raise ProvisioningWebhookError("В URL вебхука обнаружены секретные данные.")
            if info.url != webhook_url:
                raise ProvisioningWebhookError(f"URL вебхука в Telegram ({info.url}) не совпадает с ожидаемым ({webhook_url}).")

            # Success -> advance status to SETUP_REQUIRED
            bot_instance.status = BotInstanceStatus.SETUP_REQUIRED
            bot_instance.last_error = None
            await self.session.commit()
            if self.registry:
                await self.registry.invalidate_bot(bot_instance.id, reason="bot_reconnected")

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
            error_desc = f"Ошибка подключения вебхука: {redact_token(str(exc)[:200])}"
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
    async def provision_managed_bot(
        self,
        master_id: int,
        actor_user_id: int,
        bot_identity: BotIdentity,
        token: str,
        telegram_owner_user_id: Optional[int] = None,
    ) -> BotInstance:
        """Provision a managed bot created via Telegram Managed Bots flow.

        Executes full onboarding flow:
        1. Verifies Master ownership.
        2. Idempotently checks if this Telegram bot is already provisioned for this master.
        3. Encrypts token with AES-256-GCM.
        4. Persists BotInstance with provisioning_source="managed_bot", managed_by_platform=True,
           and telegram_owner_user_id.
        5. Sets webhook, default commands, and chat menu button.
        6. Advances status to SETUP_REQUIRED, writes audit log, invalidates registry.
        """
        clean_token = token.strip()

        # 1. Verify Master ownership
        master = await self.session.scalar(
            select(Master).where(Master.id == master_id).with_for_update()
        )
        if not master or master.owner_user_id != actor_user_id:
            raise AccessDeniedError("У вас нет прав на управление данным проектом.")

        # 2. Check if this bot ID is already known
        existing_bot = await self.bot_repo.get_by_telegram_bot_id(bot_identity.id)
        if existing_bot:
            if existing_bot.master_id != master_id:
                raise DuplicateBotError("Этот Telegram-бот уже подключён к платформе.")
            if existing_bot.status in (BotInstanceStatus.ACTIVE, BotInstanceStatus.SETUP_REQUIRED):
                return existing_bot

        # 3. Check current bot instance for this master
        existing_current = await self.bot_repo.get_current_for_master(master_id)
        if existing_current and existing_current.telegram_bot_id != bot_identity.id:
            await self.bot_repo.deprecate_current_for_master(master_id)

        # 4. Encrypt token via AES-256-GCM
        encrypted_token = self.crypto.encrypt(clean_token, associated_data=bot_identity.id)
        webhook_secret = secrets.token_urlsafe(32)

        # 5. Create or reuse BotInstance in PROVISIONING state
        if existing_bot:
            bot_instance = existing_bot
            bot_instance.encrypted_token = encrypted_token
            bot_instance.webhook_secret = webhook_secret
            bot_instance.status = BotInstanceStatus.PROVISIONING
            bot_instance.provisioning_source = "managed_bot"
            bot_instance.managed_by_platform = True
            bot_instance.telegram_owner_user_id = telegram_owner_user_id or actor_user_id
            bot_instance.telegram_username = bot_identity.username
            bot_instance.telegram_first_name = bot_identity.first_name
            bot_instance.is_current = True
            await self.session.commit()
        else:
            try:
                bot_instance = await self.bot_repo.create_bot_instance(
                    master_id=master_id,
                    telegram_bot_id=bot_identity.id,
                    telegram_username=bot_identity.username,
                    telegram_first_name=bot_identity.first_name,
                    encrypted_token=encrypted_token,
                    webhook_secret=webhook_secret,
                    status=BotInstanceStatus.PROVISIONING,
                    token_version=1,
                    is_current=True,
                    provisioning_source="managed_bot",
                    managed_by_platform=True,
                    telegram_owner_user_id=telegram_owner_user_id or actor_user_id,
                )
                await self.session.commit()
            except IntegrityError as exc:
                await self.session.rollback()
                orig_str = str(exc.orig) if hasattr(exc, "orig") else str(exc)
                if "telegram_bot_id" in orig_str:
                    raise DuplicateBotError("Этот Telegram-бот уже подключён к платформе.") from exc
                if "current_per_master" in orig_str:
                    raise DuplicateBotError("Для данного проекта уже выполняется подключение бота.") from exc
                raise DuplicateBotError("Конфликт при подключении бота.") from exc

        if existing_current and self.registry:
            await self.registry.invalidate_bot(existing_current.id, reason="bot_reconnected")

        await self.audit_service.log_event(
            action=AuditEvent.MANAGED_BOT_CREATED,
            actor_user_id=actor_user_id,
            master_id=master_id,
            entity_id=bot_instance.id,
            payload_after={
                "telegram_bot_id": bot_identity.id,
                "telegram_username": bot_identity.username,
                "provisioning_source": "managed_bot",
                "managed_by_platform": True,
            },
        )
        await self.session.commit()

        # 6. Configure Telegram Webhook and commands
        base_url = settings.webhook_base_url.rstrip("/")
        webhook_url = f"{base_url}/telegram/webhook/{bot_instance.public_id}"

        try:
            await self.gateway.set_webhook(
                token=clean_token,
                url=webhook_url,
                secret_token=webhook_secret,
                drop_pending_updates=False,
            )

            # Webhook verification
            info = await self.gateway.get_webhook_info(clean_token)
            if clean_token in (info.url or "") or (webhook_secret and webhook_secret in (info.url or "")):
                raise ProvisioningWebhookError("В URL вебхука обнаружены секретные данные.")
            if info.url != webhook_url:
                raise ProvisioningWebhookError(f"URL вебхука в Telegram ({info.url}) не совпадает с ожидаемым ({webhook_url}).")

            # Default commands
            try:
                await self.gateway.set_my_commands(clean_token)
            except Exception as cmd_exc:
                logger.warning("Could not set commands for bot %s: %s", bot_identity.id, cmd_exc)

            # Mini App Menu Button (if configured)
            if settings.mini_app_url:
                try:
                    await self.gateway.set_chat_menu_button(clean_token, mini_app_url=settings.mini_app_url)
                except Exception as btn_exc:
                    logger.warning("Could not set menu button for bot %s: %s", bot_identity.id, btn_exc)

            # Success -> advance status to SETUP_REQUIRED
            bot_instance.status = BotInstanceStatus.SETUP_REQUIRED
            bot_instance.last_error = None
            await self.session.commit()

            if self.registry:
                await self.registry.invalidate_bot(bot_instance.id, reason="managed_bot_provisioned")

            await self.audit_service.log_event(
                action=AuditEvent.MANAGED_BOT_PROVISIONED,
                actor_user_id=actor_user_id,
                master_id=master_id,
                entity_id=bot_instance.id,
                payload_after={"status": "SETUP_REQUIRED"},
            )
            await self.session.commit()
            return bot_instance
        except Exception as exc:
            error_desc = f"Ошибка подключения управляемого бота: {redact_token(str(exc)[:200])}"
            bot_instance.status = BotInstanceStatus.ERROR
            bot_instance.last_error = error_desc
            await self.session.commit()

            await self.audit_service.log_event(
                action=AuditEvent.MANAGED_BOT_PROVISION_FAILED,
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

        if bot_instance.status in (BotInstanceStatus.SETUP_REQUIRED, BotInstanceStatus.ACTIVE):
            return bot_instance

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

            # Webhook URL verification
            info = await self.gateway.get_webhook_info(raw_token)
            if raw_token in (info.url or "") or (bot_instance.webhook_secret and bot_instance.webhook_secret in (info.url or "")):
                raise ProvisioningWebhookError("В URL вебхука обнаружены секретные данные.")
            if info.url != webhook_url:
                raise ProvisioningWebhookError(f"URL вебхука в Telegram ({info.url}) не совпадает с ожидаемым ({webhook_url}).")

            bot_instance.status = BotInstanceStatus.SETUP_REQUIRED
            bot_instance.last_error = None
            await self.session.commit()
            if self.registry:
                await self.registry.invalidate_bot(bot_instance.id, reason="bot_reconnected")

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
            error_desc = f"Ошибка повторного подключения: {redact_token(str(exc)[:200])}"
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
        bot_instance = await self.session.scalar(
            select(BotInstance)
            .join(Master, BotInstance.master_id == Master.id)
            .where(BotInstance.id == bot_instance_id, Master.owner_user_id == actor_user_id)
            .with_for_update(of=BotInstance)
        )
        if not bot_instance:
            raise AccessDeniedError("Экземпляр бота не найден или доступ запрещен.")

        # 1. Validate getMe for new token
        identity = await self.gateway.validate_token(clean_token)

        # 2. Strict ID matching: new token must belong to the exact same Telegram bot
        if identity.id != bot_instance.telegram_bot_id:
            raise TokenRotationBotMismatchError("Это токен другого Telegram-бота.")

        if bot_instance.encrypted_token:
            stored_token = self.crypto.decrypt(
                bot_instance.encrypted_token,
                associated_data=identity.id,
            )
            if secrets.compare_digest(stored_token, clean_token):
                if bot_instance.status == BotInstanceStatus.ERROR:
                    return await self.retry_provisioning(bot_instance.id, actor_user_id)
                return bot_instance

        # 3. Encrypt new token
        new_encrypted = self.crypto.encrypt(clean_token, associated_data=identity.id)

        # 4. Advance version and save ciphertext
        bot_instance.encrypted_token = new_encrypted
        bot_instance.token_version = (bot_instance.token_version or 0) + 1
        bot_instance.telegram_username = identity.username
        bot_instance.telegram_first_name = identity.first_name
        await self.session.commit()

        # 5. Invalidate BotRegistry runtime pool and Redis bus immediately so old token v1 is discarded
        if self.registry:
            await self.registry.invalidate_bot(bot_instance.id)

        # 6. Update webhook with new token
        base_url = settings.webhook_base_url.rstrip("/")
        webhook_url = f"{base_url}/telegram/webhook/{bot_instance.public_id}"

        try:
            await self.gateway.set_webhook(
                token=clean_token,
                url=webhook_url,
                secret_token=bot_instance.webhook_secret,
            )
            # Verify webhook info (Section 14)
            info = await self.gateway.get_webhook_info(clean_token)
            if clean_token in (info.url or "") or (bot_instance.webhook_secret and bot_instance.webhook_secret in (info.url or "")):
                raise ProvisioningWebhookError("В URL вебхука обнаружены секретные данные.")
            if info.url != webhook_url:
                raise ProvisioningWebhookError(f"URL вебхука в Telegram ({info.url}) не совпадает с ожидаемым ({webhook_url}).")
        except Exception as exc:
            bot_instance.status = BotInstanceStatus.ERROR
            bot_instance.last_error = f"Ошибка вебхука при ротации: {redact_token(str(exc)[:200])}"
            await self.session.commit()
            raise ProvisioningWebhookError("Токен сохранен, но вебхук не удалось обновить.") from exc

        await self.audit_service.log_event(
            action=AuditEvent.BOT_TOKEN_ROTATED,
            actor_user_id=actor_user_id,
            master_id=bot_instance.master_id,
            entity_id=bot_instance.id,
            payload_after={"token_version": bot_instance.token_version},
        )
        await self.session.commit()
        return bot_instance

    async def rotate_managed_bot_token(
        self,
        bot_instance_id: int,
        actor_user_id: int,
    ) -> BotInstance:
        """Rotate token of a managed bot using Telegram replaceManagedBotToken API."""
        bot_instance = await self.session.scalar(
            select(BotInstance)
            .join(Master, BotInstance.master_id == Master.id)
            .where(BotInstance.id == bot_instance_id, Master.owner_user_id == actor_user_id)
        )
        if not bot_instance:
            raise AccessDeniedError("Экземпляр бота не найден или доступ запрещен.")

        if not bot_instance.managed_by_platform:
            raise AccessDeniedError("Данный бот не является управляемым платформой (Managed Bot).")

        if not bot_instance.telegram_bot_id:
            raise ProvisioningWebhookError("Управляемый бот не имеет Telegram ID.")

        if not settings.manager_bot_token:
            raise ProvisioningWebhookError("Токен платформенного бота не сконфигурирован.")

        # Request new token from Telegram via Platform Manager Bot using the managed bot ID (NOT owner ID)
        new_token = await self.gateway.replace_managed_bot_token(
            manager_token=settings.manager_bot_token.strip(),
            bot_id=bot_instance.telegram_bot_id,
        )

        return await self.rotate_token(bot_instance_id, actor_user_id, new_token)

    async def disable_bot(
        self,
        bot_instance_id: int,
        actor_user_id: int,
        *,
        commit: bool = True,
    ) -> BotInstance:
        """Disable a bot. The caller owns commit when commit=False (webhook flow)."""
        bot_instance = await self._locked_bot(bot_instance_id, actor_user_id)
        if not bot_instance:
            raise AccessDeniedError("Экземпляр бота не найден или доступ запрещен.")
        if bot_instance.status == BotInstanceStatus.DISABLED:
            return bot_instance

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
                bot_instance.last_error = f"Ошибка отзыва вебхука в Telegram: {redact_token(str(exc)[:200])}"

        # 2. Update status in database
        bot_instance.status = BotInstanceStatus.DISABLED
        await self.audit_service.log_event(
            action=AuditEvent.BOT_DISABLED,
            actor_user_id=actor_user_id,
            master_id=bot_instance.master_id,
            entity_id=bot_instance.id,
            payload_after={"status": "DISABLED"},
        )
        await self._finish_state_change(bot_instance.id, "manual", commit=commit)
        return bot_instance

    async def enable_bot(
        self,
        bot_instance_id: int,
        actor_user_id: int,
        *,
        commit: bool = True,
    ) -> BotInstance:
        """Re-enable a bot. The caller owns commit when commit=False."""
        bot_instance = await self._locked_bot(bot_instance_id, actor_user_id)
        if not bot_instance:
            raise AccessDeniedError("Экземпляр бота не найден или доступ запрещен.")
        if bot_instance.status in (BotInstanceStatus.SETUP_REQUIRED, BotInstanceStatus.ACTIVE):
            return bot_instance

        if not bot_instance.encrypted_token or not bot_instance.telegram_bot_id:
            raise ProvisioningWebhookError("Токен бота не настроен.")

        raw_token = self.crypto.decrypt(
            bot_instance.encrypted_token,
            associated_data=bot_instance.telegram_bot_id,
        )

        try:
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

            # Webhook URL verification
            info = await self.gateway.get_webhook_info(raw_token)
            if raw_token in (info.url or "") or (bot_instance.webhook_secret and bot_instance.webhook_secret in (info.url or "")):
                raise ProvisioningWebhookError("В URL вебхука обнаружены секретные данные.")
            if info.url != webhook_url:
                raise ProvisioningWebhookError(f"URL вебхука в Telegram ({info.url}) не совпадает с ожидаемым ({webhook_url}).")

            bot_instance.status = BotInstanceStatus.SETUP_REQUIRED
            bot_instance.last_error = None
        except Exception as exc:
            bot_instance.status = BotInstanceStatus.ERROR
            bot_instance.last_error = f"Ошибка включения бота: {redact_token(str(exc)[:200])}"
            if commit:
                await self.session.commit()
            raise ProvisioningWebhookError(bot_instance.last_error) from exc

        await self.audit_service.log_event(
            action=AuditEvent.BOT_ENABLED,
            actor_user_id=actor_user_id,
            master_id=bot_instance.master_id,
            entity_id=bot_instance.id,
            payload_after={"status": "SETUP_REQUIRED"},
        )
        await self._finish_state_change(bot_instance.id, "manual", commit=commit)
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

        bot_instance = await self.bot_repo.get_current_for_master(master_id)
        if master.status == MasterStatus.ACTIVE and bot_instance and bot_instance.status == BotInstanceStatus.ACTIVE:
            return True, []

        is_ready, missing = await self.readiness_service.check(master_id)
        if not is_ready:
            return False, missing

        # Readiness passed -> activate
        master.status = MasterStatus.ACTIVE
        if bot_instance and bot_instance.status == BotInstanceStatus.SETUP_REQUIRED:
            bot_instance.status = BotInstanceStatus.ACTIVE

        try:
            await self.session.flush()
        except IntegrityError as exc:
            await self.session.rollback()
            orig_str = str(exc.orig) if hasattr(exc, "orig") else str(exc)
            if "uq_bot_instances_active_per_master" in orig_str:
                return False, ["У этого мастера уже есть активный бот."]
            return False, ["Ошибка активации. Обратитесь в поддержку."]

        if bot_instance and self.registry:
            await self.registry.invalidate_bot(bot_instance.id, reason="bot_activated")

        await self.audit_service.log_event(
            action=AuditEvent.MASTER_ACTIVATED,
            actor_user_id=actor_user_id,
            master_id=master_id,
            entity_id=bot_instance.id if bot_instance else None,
            payload_after={"master_status": "ACTIVE", "bot_status": "ACTIVE"},
        )
        await self.session.flush()
        return True, []

    async def delete_bot(
        self,
        bot_instance_id: int,
        actor_user_id: int,
        is_platform_admin: bool = False,
        *,
        commit: bool = True,
    ) -> BotInstance:
        """Logically delete / unlink a customer bot instance and revoke its webhook."""
        bot_instance = await self._locked_bot(bot_instance_id, actor_user_id, is_platform_admin)

        if not bot_instance:
            raise AccessDeniedError("Экземпляр бота не найден или доступ запрещен.")
        if bot_instance.status == BotInstanceStatus.DISABLED and not bot_instance.is_current:
            return bot_instance

        # 1. Best-effort deleteWebhook in Telegram
        if bot_instance.encrypted_token and bot_instance.telegram_bot_id:
            try:
                raw_token = self.crypto.decrypt(
                    bot_instance.encrypted_token,
                    associated_data=bot_instance.telegram_bot_id,
                )
                await self.gateway.delete_webhook(raw_token)
            except Exception as exc:
                logger.warning(
                    "deleteWebhook failed during delete_bot for bot #%s (continuing): %s",
                    bot_instance.id,
                    exc,
                )

        # 2. Update status and unlink: mark disabled and not current
        bot_instance.status = BotInstanceStatus.DISABLED
        bot_instance.is_current = False
        bot_instance.last_error = "Бот отключён и удалён из проекта."

        # If master status was ACTIVE, switch to SETUP_REQUIRED since no active bot remains
        master = await self.master_repo.get_by_id(bot_instance.master_id)
        if master and master.status == MasterStatus.ACTIVE:
            master.status = MasterStatus.SETUP_REQUIRED

        # Audit and state are committed together by the transaction owner.
        await self.audit_service.log_event(
            action=AuditEvent.BOT_DELETED,
            actor_user_id=actor_user_id,
            master_id=bot_instance.master_id,
            entity_id=bot_instance.id,
            payload_after={"status": "DISABLED", "is_current": False},
        )
        await self._finish_state_change(bot_instance.id, "bot_deleted", commit=commit)
        return bot_instance

    async def _locked_bot(
        self, bot_instance_id: int, actor_user_id: int, is_platform_admin: bool = False,
    ) -> BotInstance | None:
        """Serialize state changes and refresh any identity-map copy loaded by a handler."""
        stmt = select(BotInstance).where(BotInstance.id == bot_instance_id)
        if not is_platform_admin:
            stmt = stmt.join(Master, BotInstance.master_id == Master.id).where(
                Master.owner_user_id == actor_user_id
            )
        return await self.session.scalar(
            stmt.with_for_update(of=BotInstance).execution_options(populate_existing=True)
        )

    async def _finish_state_change(self, bot_id: int, reason: str, *, commit: bool) -> None:
        await self.session.flush()
        async def invalidate() -> None:
            if self.registry:
                if reason == "manual":
                    await self.registry.invalidate_bot(bot_id)
                else:
                    await self.registry.invalidate_bot(bot_id, reason=reason)
        if commit:
            await self.session.commit()
            await invalidate()
        elif self.registry:
            # Publish only after the middleware has committed state and its update ledger.
            self.session.info.setdefault("post_commit", []).append(invalidate)
