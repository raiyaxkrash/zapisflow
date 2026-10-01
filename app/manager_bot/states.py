"""FSM states for platform Manager Bot."""

from aiogram.fsm.state import State, StatesGroup


class CreateMasterStates(StatesGroup):
    """FSM states for creating a new business/master project."""

    waiting_for_name = State()


class ConnectBotStates(StatesGroup):
    """FSM states for onboarding a new Telegram bot."""

    waiting_for_token = State()
    confirm_connect = State()


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
