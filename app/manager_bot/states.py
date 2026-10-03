"""FSM states for platform Manager Bot."""

from aiogram.fsm.state import State, StatesGroup


class CreateMasterStates(StatesGroup):
    """FSM states for creating a new business/master project."""

    waiting_for_name = State()


class ConnectBotStates(StatesGroup):
    """FSM states for onboarding a new Telegram bot."""

    waiting_for_token = State()
    confirm_connect = State()


class ManagedBotStates(StatesGroup):
    """FSM states for official Telegram Managed Bots onboarding flow."""

    waiting_for_creation = State()
    waiting_for_custom_username = State()



class RotateTokenStates(StatesGroup):
    """FSM states for rotating bot API token."""

    waiting_for_token = State()


class CrmSearchStates(StatesGroup):
    """FSM states for client search in CRM."""

    waiting_for_query = State()


class CrmNoteStates(StatesGroup):
    """FSM states for editing client note."""

    waiting_for_note = State()


class MasterContactStates(StatesGroup):
    """FSM states for editing master contacts in Manager Bot."""

    waiting_for_value = State()


class MasterOnboardingStates(StatesGroup):
    """FSM states for guided onboarding wizard."""

    waiting_for_activity_type = State()
    waiting_for_address = State()
    waiting_for_phone = State()
    waiting_for_service_title = State()
    waiting_for_service_price = State()
    waiting_for_service_duration = State()
    waiting_for_schedule = State()


class ManagerServiceStates(StatesGroup):
    """FSM states for managing services in Manager Bot."""

    waiting_for_title = State()
    waiting_for_price = State()
    waiting_for_duration = State()
    waiting_for_buffer = State()
    waiting_for_deposit_value = State()
    waiting_for_edit_field_value = State()


class ManagerPortfolioStates(StatesGroup):
    """FSM states for managing portfolio in Manager Bot."""

    waiting_for_category_title = State()
    waiting_for_photo = State()
    waiting_for_caption = State()


class ManagerScheduleStates(StatesGroup):
    """FSM states for managing schedule in Manager Bot."""

    waiting_for_hours = State()
    waiting_for_break = State()
    waiting_for_day_off_date = State()
    waiting_for_work_date = State()
    waiting_for_work_hours = State()
    waiting_for_horizon_days = State()
    waiting_for_advance_hours = State()


class ManagerSettingsStates(StatesGroup):
    """FSM states for editing general project settings."""

    waiting_for_name = State()
    waiting_for_description = State()
    waiting_for_prepay_value = State()


class ManagerStaffStates(StatesGroup):
    """FSM states for managing studio staff members in Manager Bot."""

    waiting_for_name = State()
    waiting_for_specialization = State()
    waiting_for_description = State()
    waiting_for_edit_field_value = State()


class AdminPlanStates(StatesGroup):
    """FSM states for editing subscription plans."""

    waiting_for_name = State()
    waiting_for_price = State()
    waiting_for_price_confirm = State()
    waiting_for_custom_duration = State()
    waiting_for_sort_order = State()


class AdminSubscriptionStates(StatesGroup):
    """FSM states for manual subscription management."""

    waiting_for_custom_days = State()
    waiting_for_custom_months = State()
    waiting_for_expiry_date = State()
    waiting_for_custom_reason = State()


class AdminBotStates(StatesGroup):
    """FSM states for bot administrative actions."""

    waiting_for_delete_confirm = State()


class AdminProjectStates(StatesGroup):
    """FSM states for project administrative actions."""

    waiting_for_delete_confirm = State()
