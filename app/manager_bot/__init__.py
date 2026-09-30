"""Manager bot package export."""

from app.manager_bot.dispatcher import create_manager_dispatcher
from app.manager_bot.handlers import manager_router

__all__ = ["create_manager_dispatcher", "manager_router"]
