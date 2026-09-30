"""
Admin keyboards and callbacks export.
"""

from app.bot.keyboards.admin.callbacks import (
    AdminMenuCallback,
    AdminAppointmentCallback,
    AdminPaymentCallback,
    AdminCalendarCallback,
    AdminServiceCallback,
    AdminClientCallback,
)
from app.bot.keyboards.admin.menu import (
    get_admin_dashboard_keyboard,
    get_admin_back_keyboard,
)
from app.bot.keyboards.admin.appointments import (
    get_appointments_filters_keyboard,
    get_admin_appointment_list_keyboard,
    get_admin_appointment_card_keyboard,
)
from app.bot.keyboards.admin.schedule import (
    get_admin_calendar_keyboard,
    get_admin_day_management_keyboard,
)
from app.bot.keyboards.admin.services import (
    get_admin_services_list_keyboard,
    get_admin_service_card_keyboard,
)
from app.bot.keyboards.admin.clients import (
    get_admin_clients_menu_keyboard,
    get_admin_client_card_keyboard,
)

__all__ = [
    "AdminMenuCallback",
    "AdminAppointmentCallback",
    "AdminPaymentCallback",
    "AdminCalendarCallback",
    "AdminServiceCallback",
    "AdminClientCallback",
    "get_admin_dashboard_keyboard",
    "get_admin_back_keyboard",
    "get_appointments_filters_keyboard",
    "get_admin_appointment_list_keyboard",
    "get_admin_appointment_card_keyboard",
    "get_admin_calendar_keyboard",
    "get_admin_day_management_keyboard",
    "get_admin_services_list_keyboard",
    "get_admin_service_card_keyboard",
    "get_admin_clients_menu_keyboard",
    "get_admin_client_card_keyboard",
]
