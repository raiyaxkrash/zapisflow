"""
FSM states for administrative workflows.
"""

from aiogram.fsm.state import State, StatesGroup


class AdminServiceSG(StatesGroup):
    """
    Service creation and editing flow.
    """
    title = State()
    description = State()
    price = State()
    duration = State()
    buffer = State()
    deposit_type = State()
    deposit_value = State()
    edit_field_value = State()


class AdminScheduleDaySG(StatesGroup):
    """
    Date overrides, custom hours and manual time blocking.
    """
    picking_date = State()
    setting_hours = State()
    adding_break = State()
    blocking_slot_start = State()
    blocking_slot_end = State()
    blocking_reason = State()


class AdminManualBookingSG(StatesGroup):
    """
    Manual appointment creation by admin (e.g. phone call).
    """
    choosing_service = State()
    choosing_date = State()
    choosing_time = State()
    client_name = State()
    client_phone = State()
    client_username = State()
    admin_notes = State()


class AdminRescheduleSG(StatesGroup):
    """
    Admin rescheduling an existing appointment.
    """
    picking_date = State()
    picking_time = State()
    reason = State()


class AdminAppointmentNoteSG(StatesGroup):
    """
    Adding an internal master note to an appointment.
    """
    entering_note = State()


class AdminClientNoteSG(StatesGroup):
    """
    Adding an internal note to a client profile.
    """
    entering_note = State()


class AdminClientSearchSG(StatesGroup):
    """
    Searching client CRM by query.
    """
    entering_query = State()


class AdminSettingsSG(StatesGroup):
    """
    Editing studio configuration settings.
    """
    editing_value = State()


class AdminRejectPaymentSG(StatesGroup):
    """
    Entering custom rejection explanation for payment receipt.
    """
    entering_reason = State()


class AdminBroadcastSG(StatesGroup):
    """
    Broadcast campaign creation wizard.
    """
    entering_text = State()
    attaching_photo = State()
    setting_button = State()
    confirming = State()

