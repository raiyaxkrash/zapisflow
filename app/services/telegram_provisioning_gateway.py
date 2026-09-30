"""Gateway abstraction for Telegram Bot API provisioning calls.

Provides reliable and clean token validation, webhook configuration, inspection, and deletion,
guaranteeing that temporary bot client sessions are closed cleanly without resource leaks.
"""

import asyncio
from dataclasses import dataclass
import logging
from typing import Optional

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import (
    TelegramAPIError,
    TelegramNetworkError,
    TelegramUnauthorizedError,
)
from aiogram.types import WebhookInfo

from app.core.security import mask_token
from app.services.exceptions import (
    InvalidBotTokenError,
    TelegramGatewayError,
    TelegramGatewayNetworkError,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class BotIdentity:
    """Public identity metadata of a validated Telegram bot."""

    id: int
    username: Optional[str]
    first_name: str
    can_join_groups: bool = False
    can_read_all_group_messages: bool = False
    supports_inline_queries: bool = False


class TelegramProvisioningGateway:
    """Telegram Bot API abstraction for tenant bot lifecycle management."""

    def __init__(self, request_timeout: float = 10.0) -> None:
        self.request_timeout = request_timeout

    def _create_temp_bot(self, token: str) -> Bot:
        """Create isolated, temporary aiogram Bot instance."""
        return Bot(
            token=token,
            default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        )

    async def validate_token(self, token: str) -> BotIdentity:
        """Call Telegram getMe to validate the token and return bot identity.

        Always closes the temporary bot session in a finally block.
        """
        bot = self._create_temp_bot(token)
        try:
            me = await asyncio.wait_for(bot.get_me(), timeout=self.request_timeout)
            logger.info("Validated Telegram bot @%s (#%s)", me.username, me.id)
            return BotIdentity(
                id=me.id,
                username=me.username,
                first_name=me.first_name,
                can_join_groups=me.can_join_groups or False,
                can_read_all_group_messages=me.can_read_all_group_messages or False,
                supports_inline_queries=me.supports_inline_queries or False,
            )
        except TelegramUnauthorizedError as exc:
            logger.warning("Token unauthorized for bot token %s", mask_token(token))
            raise InvalidBotTokenError("Токен недействителен или был отозван в BotFather.") from exc
        except (TelegramNetworkError, asyncio.TimeoutError) as exc:
            logger.error("Network timeout while querying getMe for %s", mask_token(token))
            raise TelegramGatewayNetworkError("Таймаут соединения с Telegram Bot API.") from exc
        except TelegramAPIError as exc:
            logger.error("Telegram API error validating token %s: %s", mask_token(token), exc)
            raise TelegramGatewayError(f"Ошибка Telegram API: {exc.message}") from exc
        finally:
            await bot.session.close()

    async def set_webhook(
        self,
        token: str,
        url: str,
        secret_token: Optional[str] = None,
        allowed_updates: Optional[list[str]] = None,
        drop_pending_updates: bool = False,
    ) -> bool:
        """Configure Telegram webhook endpoint with secret token verification.

        Always closes the temporary bot session in a finally block.
        """
        bot = self._create_temp_bot(token)
        try:
            result = await asyncio.wait_for(
                bot.set_webhook(
                    url=url,
                    secret_token=secret_token,
                    allowed_updates=allowed_updates,
                    drop_pending_updates=drop_pending_updates,
                ),
                timeout=self.request_timeout,
            )
            logger.info("Successfully set webhook for bot token %s -> %s", mask_token(token), url)
            return bool(result)
        except TelegramUnauthorizedError as exc:
            logger.warning("Token unauthorized setting webhook for %s", mask_token(token))
            raise InvalidBotTokenError("Токен недействителен.") from exc
        except (TelegramNetworkError, asyncio.TimeoutError) as exc:
            logger.error("Network error setting webhook for %s: %s", mask_token(token), exc)
            raise TelegramGatewayNetworkError("Ошибка сети при установке вебхука.") from exc
        except TelegramAPIError as exc:
            logger.error("Telegram API error setting webhook for %s: %s", mask_token(token), exc)
            raise TelegramGatewayError(f"Ошибка установки вебхука: {exc.message}") from exc
        finally:
            await bot.session.close()

    async def get_webhook_info(self, token: str) -> WebhookInfo:
        """Inspect current webhook configuration for the bot.

        Always closes the temporary bot session in a finally block.
        """
        bot = self._create_temp_bot(token)
        try:
            info = await asyncio.wait_for(bot.get_webhook_info(), timeout=self.request_timeout)
            return info
        except TelegramUnauthorizedError as exc:
            raise InvalidBotTokenError("Токен недействителен.") from exc
        except (TelegramNetworkError, asyncio.TimeoutError) as exc:
            raise TelegramGatewayNetworkError("Ошибка сети при проверке вебхука.") from exc
        except TelegramAPIError as exc:
            raise TelegramGatewayError(f"Ошибка проверки вебхука: {exc.message}") from exc
        finally:
            await bot.session.close()

    async def delete_webhook(self, token: str, drop_pending_updates: bool = False) -> bool:
        """Remove webhook from Telegram Bot API.

        Always closes the temporary bot session in a finally block.
        """
        bot = self._create_temp_bot(token)
        try:
            res = await asyncio.wait_for(
                bot.delete_webhook(drop_pending_updates=drop_pending_updates),
                timeout=self.request_timeout,
            )
            logger.info("Deleted webhook for bot token %s", mask_token(token))
            return bool(res)
        except TelegramUnauthorizedError as exc:
            # If token already revoked on BotFather side, deleteWebhook fails with unauthorized
            logger.warning("Token already revoked when deleting webhook: %s", mask_token(token))
            raise InvalidBotTokenError("Токен отозван.") from exc
        except (TelegramNetworkError, asyncio.TimeoutError) as exc:
            logger.error("Network error deleting webhook for %s: %s", mask_token(token), exc)
            raise TelegramGatewayNetworkError("Ошибка сети при удалении вебхука.") from exc
        except TelegramAPIError as exc:
            logger.error("Telegram API error deleting webhook: %s", exc)
            raise TelegramGatewayError(f"Ошибка удаления вебхука: {exc.message}") from exc
        finally:
            await bot.session.close()
