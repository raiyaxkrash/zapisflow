"""Preview or update one explicitly selected current client bot's system menu.

Run from the repository root: python -m scripts.update_client_menu --help
No database changes, token rotation or bulk updates are performed.
"""
import argparse
import asyncio

from sqlalchemy import select

from app.config.settings import settings
from app.config.url_validation import miniapp_origin
from app.database.session import async_session_factory
from app.database.models.master import BotInstance, BotInstanceStatus, Master
from app.services.bot_provisioning_service import BotProvisioningService


async def update(args):
    async with async_session_factory() as session:
        bot = await session.scalar(
            select(BotInstance).join(Master, Master.id == BotInstance.master_id).where(
                BotInstance.id == args.bot_instance_id,
                Master.owner_user_id == args.owner_user_id,
                BotInstance.is_current.is_(True),
                BotInstance.status.in_([BotInstanceStatus.ACTIVE, BotInstanceStatus.SETUP_REQUIRED]),
            )
        )
        if bot is None:
            raise SystemExit("No eligible current bot for the specified owner")
        if bot.mini_app_enabled and settings.mini_app_base_url:
            print(f"BotInstance {bot.id}: ZapisFlow -> {miniapp_origin(settings.mini_app_base_url)}/b/{bot.public_id}")
        else:
            print(f"BotInstance {bot.id}: standard command menu (Mini App disabled/unconfigured)")
        if not args.apply:
            print("Preview only. Add --apply to update this one bot.")
            return
        service = BotProvisioningService(session)
        token = service.crypto.decrypt(bot.encrypted_token, associated_data=bot.telegram_bot_id)
        if not await service.configure_client_menu(bot, token):
            raise SystemExit("Telegram menu update failed")
        print("System menu updated")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bot-instance-id", type=int, required=True)
    parser.add_argument("--owner-user-id", type=int, required=True, help="Internal database User.id")
    parser.add_argument("--apply", action="store_true")
    asyncio.run(update(parser.parse_args()))


if __name__ == "__main__":
    main()
