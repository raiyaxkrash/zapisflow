"""
Admin routers aggregation module.
"""

from aiogram import Router

from app.bot.handlers.admin.analytics import router as analytics_router
from app.bot.handlers.admin.appointments import router as appointments_router
from app.bot.handlers.admin.broadcast import router as broadcast_router
from app.bot.handlers.admin.calendar_mgmt import router as calendar_router
from app.bot.handlers.admin.clients_mgmt import router as clients_router
from app.bot.handlers.admin.contacts_mgmt import router as contacts_router
from app.bot.handlers.admin.dashboard import router as dashboard_router
from app.bot.handlers.admin.manual_booking import router as manual_booking_router
from app.bot.handlers.admin.payments import router as payments_router
from app.bot.handlers.admin.services_mgmt import router as services_router
from app.bot.handlers.admin.settings_mgmt import router as settings_router

admin_router = Router(name="admin_root")

admin_router.include_router(dashboard_router)
admin_router.include_router(payments_router)
admin_router.include_router(appointments_router)
admin_router.include_router(calendar_router)
admin_router.include_router(manual_booking_router)
admin_router.include_router(services_router)
admin_router.include_router(clients_router)
admin_router.include_router(settings_router)
admin_router.include_router(contacts_router)
admin_router.include_router(analytics_router)
admin_router.include_router(broadcast_router)

__all__ = ["admin_router"]
