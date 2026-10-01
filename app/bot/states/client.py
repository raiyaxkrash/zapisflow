"""
FSM states for the client booking and checkout flows.
"""

from aiogram.fsm.state import State, StatesGroup


class ClientBookingSG(StatesGroup):
    """
    States for client appointment reservation and proof upload.
    """
    choosing_service = State()      # Browsing and selecting service
    choosing_date = State()         # Calendar date selection
    choosing_time = State()         # Time slot selection
    entering_phone = State()        # Sharing contact phone number
    confirming_policy = State()     # Acknowledging cancellation & deposit policy
    waiting_payment = State()       # Payment instructions screen
    uploading_proof = State()       # Uploading receipt screenshot or document


class ClientReviewSG(StatesGroup):
    """States for client post-visit review and rating."""

    waiting_for_comment = State()
