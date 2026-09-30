"""
Client handlers package router aggregator.
"""

from aiogram import Router

from app.bot.handlers.client.start import router as start_router
from app.bot.handlers.client.services import router as services_router
from app.bot.handlers.client.booking import router as booking_router
from app.bot.handlers.client.payment import router as payment_router
from app.bot.handlers.client.my_appointments import router as my_appointments_router
from app.bot.handlers.client.portfolio import router as portfolio_router
from app.bot.handlers.client.about import router as about_router

client_router = Router(name="client_root")
client_router.include_router(start_router)
client_router.include_router(services_router)
client_router.include_router(booking_router)
client_router.include_router(payment_router)
client_router.include_router(my_appointments_router)
client_router.include_router(portfolio_router)
client_router.include_router(about_router)

__all__ = ["client_router"]
