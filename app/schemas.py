import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models import BookingStatus


class ActivityIn(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=2000)


class ActivityOut(ActivityIn):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID


class SlotIn(BaseModel):
    starts_at: datetime
    capacity: int = Field(gt=0, le=500)
    price_idr: int = Field(ge=0)


class SlotOut(BaseModel):
    id: uuid.UUID
    activity_id: uuid.UUID
    starts_at: datetime
    capacity: int
    price_idr: int
    seats_available: int


class BookingIn(BaseModel):
    customer_name: str = Field(min_length=1, max_length=160)
    customer_whatsapp: str = Field(pattern=r"^\+?[0-9]{8,15}$")
    party_size: int = Field(gt=0, le=50)


class BookingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    slot_id: uuid.UUID
    customer_name: str
    customer_whatsapp: str
    party_size: int
    total_idr: int
    status: BookingStatus
    hold_expires_at: datetime
    paid_idr: int
    refund_due_idr: int


class BookingCreated(BookingOut):
    # Shown with the booking when it is created. Send it as X-Booking-Token to read or cancel it.
    access_token: str


class ChargeOut(BaseModel):
    reference: str
    payment_url: str
    amount_idr: int  # what is still to be paid
    expires_at: datetime


class PaymentEventIn(BaseModel):
    event_id: str = Field(min_length=1, max_length=120)
    booking_id: uuid.UUID
    status: str = Field(pattern="^(paid|failed)$")
    amount_idr: int = Field(ge=0)


class WebhookResult(BaseModel):
    result: str
