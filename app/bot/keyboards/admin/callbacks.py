"""
Callback data factories for admin inline keyboards.
"""

from aiogram.filters.callback_data import CallbackData


class AdminMenuCallback(CallbackData, prefix="adm"):
    action: str  # "dashboard", "appointments", "payments", "calendar", "services", "clients", "settings", "exit"


class AdminAppointmentCallback(CallbackData, prefix="adm_app"):
    action: str  # "list", "detail", "cancel", "complete", "no_show", "reschedule", "note"
    appointment_id: int = 0
    filter_type: str = ""  # "today", "tomorrow", "upcoming", "pending_payment", "pending_proof", "completed", "cancelled"


class AdminPaymentCallback(CallbackData, prefix="adm_pay"):
    action: str  # "inbox", "approve", "reject", "reject_preset"
    payment_id: int = 0
    preset_reason: str = ""


class AdminCalendarCallback(CallbackData, prefix="adm_cal"):
    action: str  # "month", "day", "toggle_day_off", "set_hours", "block_slot", "manual_book", "day_bookings"
    year: int = 0
    month: int = 0
    day: int = 0


class AdminServiceCallback(CallbackData, prefix="adm_svc"):
    action: str  # "list", "detail", "add", "edit", "toggle", "archive", "unarchive"
    service_id: int = 0
    field: str = ""  # "title", "price", "duration", "buffer", "deposit"


class AdminClientCallback(CallbackData, prefix="adm_cli"):
    action: str  # "list", "detail", "search", "note", "history"
    user_id: int = 0
