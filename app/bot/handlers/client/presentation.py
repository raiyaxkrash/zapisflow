"""Current project presentation for menus, including callbacks from old messages."""

from pydantic import ValidationError

from app.repositories.master_settings_repository import MasterSettingsRepository
from app.services.branding import BrandingInput


async def project_brand(session, master_id):
    config = await MasterSettingsRepository(session).get_by_master_id(master_id)
    return brand_from_settings(config)


def brand_from_settings(config):
    try:
        return BrandingInput.model_validate(
            config.branding if config else {}
        ).model_dump()
    except (ValidationError, TypeError):
        return BrandingInput().model_dump()


async def project_menu(session, master_id, is_admin=False):
    from app.bot.handlers.client.start import client_menu

    return client_menu(is_admin, await project_brand(session, master_id))


async def section_available(callback, session, master_id, section, *, brand=None):
    if brand is None:
        brand = await project_brand(session, master_id)
    if not brand["show_" + section]:
        await callback.answer("Этот раздел сейчас недоступен", show_alert=True)
        return False
    return True
