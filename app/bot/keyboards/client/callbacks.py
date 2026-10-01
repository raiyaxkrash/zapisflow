"""
Callback data factories for client inline keyboards.
"""

from aiogram.filters.callback_data import CallbackData


class MenuCallback(CallbackData, prefix="menu"):
    action: str  # "main", "book", "services", "portfolio", "about", "my_bookings", "contact"


class ServiceCallback(CallbackData, prefix="svc"):
    action: str  # "view", "select", "list"
    service_id: int


class StaffChoiceCallback(CallbackData, prefix="stf"):
    action: str  # "select", "any"
    staff_id: int = 0


class CalendarNavCallback(CallbackData, prefix="cal"):
    action: str  # "prev_month", "next_month", "select_day", "ignore"
    year: int
    month: int
    day: int = 0


class TimeSlotCallback(CallbackData, prefix="slot"):
    service_id: int
    timestamp: int  # UNIX timestamp of slot start


class BookingActionCallback(CallbackData, prefix="book"):
    action: str  # "agree_policy", "cancel_policy", "i_paid", "cancel_booking", "detail", "client_cancel"
    appointment_id: int = 0


class PortfolioNavCallback(CallbackData, prefix="port"):
    action: str  # "categories", "category", "next", "prev"
    category_id: int = 0
    item_index: int = 0
