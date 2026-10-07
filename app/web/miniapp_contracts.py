"""Input allow-lists for the Mini App. Extra authoritative fields are rejected."""

from typing import Literal
from datetime import date, datetime, time
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.database.models.service import DepositType
from app.services.master_contacts import contact_phone_e164


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ServiceOutput(BaseModel):
    id: int
    title: str
    description: str | None
    price: str
    duration_min: int
    buffer_min: int
    deposit_type: str
    deposit_value: str
    is_active: bool


class StaffOutput(BaseModel):
    id: int
    display_name: str
    specialization: str | None
    is_active: bool


class ProofOutput(BaseModel):
    id: int
    media_type: str


class PaymentOutput(BaseModel):
    id: int
    status: str
    amount: str
    rejection_reason: str | None
    proofs: list[ProofOutput]


class AppointmentOutput(BaseModel):
    duration_min: int | None = None
    id: int
    service: str
    staff: str
    start_time: str
    status: str
    status_label: str
    price: str
    deposit: str
    hold_until: str | None
    policy_agreed: bool
    cancel_allowed: bool
    cancel_reason: str | None
    cancel_consequences: str
    payment: list[PaymentOutput]
    client: str | None = None
    phone: str | None = None
    notes: str | None = None
    source: str | None = None


class SlotsOutput(BaseModel):
    timezone: str
    slots: list[str]


class AuthInput(Input):
    bot_public_id: UUID
    init_data: str = Field(min_length=1, max_length=16384, repr=False)


class HoldInput(Input):
    service_id: int = Field(gt=0)
    staff_id: int | None = Field(default=None, gt=0)
    start_time: datetime

    @field_validator("start_time")
    @classmethod
    def aware_time(cls, v):
        if v.tzinfo is None:
            raise ValueError("Timezone required")
        return v


class ConfirmInput(Input):
    appointment_id: int = Field(gt=0)
    phone: str = Field(min_length=10, max_length=64)
    policy_agreed: bool

    @field_validator("phone")
    @classmethod
    def phone_number(cls, v):
        normalized = contact_phone_e164(v)
        if normalized is None:
            raise ValueError("Invalid phone")
        return normalized


class ManualInput(HoldInput):
    master_client_id: int = Field(gt=0)
    notes: str | None = Field(default=None, max_length=2000)
    phone: str | None = Field(default=None, max_length=64)

    @field_validator("phone")
    @classmethod
    def optional_phone(cls, value):
        if value is None:
            return None
        return ConfirmInput.phone_number(value)


class NotesInput(Input):
    notes: str | None = Field(default=None, max_length=2000)


class DecisionInput(Input):
    approve: bool
    reason: str = Field(default="Чек отклонён мастером", min_length=1, max_length=255)


class ServiceInput(Input):
    title: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=2000)
    price: Decimal = Field(ge=0, le=99999999, decimal_places=2)
    duration_min: int = Field(gt=0, le=1440)
    buffer_min: int = Field(ge=0, le=1440)
    deposit_type: DepositType = DepositType.FIXED
    deposit_value: Decimal = Field(
        default=Decimal(0), ge=0, le=99999999, decimal_places=2
    )
    is_active: bool = True

    @model_validator(mode="after")
    def deposit_bounds(self):
        if self.deposit_type == DepositType.PERCENT and self.deposit_value > 100:
            raise ValueError("Percent exceeds 100")
        if self.deposit_type == DepositType.FIXED and self.deposit_value > self.price:
            raise ValueError("Deposit exceeds price")
        if not self.title.strip():
            raise ValueError("Empty title")
        return self


class StaffInput(Input):
    display_name: str = Field(min_length=1, max_length=128)
    specialization: str | None = Field(default=None, max_length=128)
    is_active: bool = True
    service_ids: list[int] = Field(default_factory=list, max_length=100)

    @field_validator("display_name")
    @classmethod
    def nonempty_name(cls, value):
        if not value.strip():
            raise ValueError("Empty name")
        return value.strip()


class SettingsInput(Input):
    studio_address: str | None = Field(default=None, max_length=500)
    studio_phone: str | None = Field(default=None, max_length=64)
    whatsapp_phone: str | None = Field(default=None, max_length=64)
    working_hours_text: str | None = Field(default=None, max_length=300)
    contacts_intro_text: str | None = Field(default=None, max_length=1000)
    telegram_username: str | None = Field(default=None, max_length=64)
    vk_profile: str | None = Field(default=None, max_length=128)
    about_text: str | None = Field(default=None, max_length=2000)
    bank_name: str | None = Field(default=None, max_length=128)
    bank_card_number: str | None = Field(default=None, max_length=64)
    bank_recipient_name: str | None = Field(default=None, max_length=128)
    booking_horizon_days: int | None = Field(default=None, ge=1, le=365)
    hold_duration_minutes: int | None = Field(default=None, ge=5, le=120)
    cancel_policy_hours: int | None = Field(default=None, ge=0, le=168)
    min_advance_hours: int | None = Field(default=None, ge=0, le=168)
    grid_step_minutes: int | None = Field(default=None, ge=5, le=120)
    default_buffer_minutes: int | None = Field(default=None, ge=0, le=240)
    reminder_24h_enabled: bool | None = None
    reminder_3h_enabled: bool | None = None

    @model_validator(mode="after")
    def required_non_null(self):
        for name in self.model_fields_set:
            if (
                name.endswith(("_days", "_minutes", "_hours", "_enabled"))
                and getattr(self, name) is None
            ):
                raise ValueError("Setting cannot be null")
        return self


class ScheduleInput(Input):
    staff_id: int = Field(gt=0)
    weekday: int | None = Field(default=None, ge=0, le=6)
    target_date: date | None = None
    is_day_off: bool = False
    work_start: time = time(10)
    work_end: time = time(19)
    breaks: list[tuple[time, time]] = Field(default_factory=list, max_length=10)

    @model_validator(mode="after")
    def window(self):
        if (self.weekday is None) == (self.target_date is None):
            raise ValueError("Choose weekday OR date")
        if self.work_start >= self.work_end:
            raise ValueError("Invalid work interval")
        ordered = sorted(self.breaks)
        for index, (start, end) in enumerate(ordered):
            if not self.work_start <= start < end <= self.work_end:
                raise ValueError("Break outside work interval")
            if index and ordered[index - 1][1] > start:
                raise ValueError("Overlapping breaks")
        return self


class DateScheduleInput(Input):
    staff_id: int = Field(gt=0)
    scope: Literal["staff", "project"] = "staff"
    mode: Literal["weekly", "day_off", "custom"]
    work_start: time = time(10)
    work_end: time = time(19)
    breaks: list[tuple[time, time]] = Field(default_factory=list, max_length=10)

    @model_validator(mode="after")
    def window(self):
        if self.mode == "custom":
            ScheduleInput(staff_id=self.staff_id, weekday=0, work_start=self.work_start,
                          work_end=self.work_end, breaks=self.breaks)
        return self
