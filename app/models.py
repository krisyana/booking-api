import enum
import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    Uuid,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class BookingStatus(enum.StrEnum):
    PENDING = "pending"  # seats held until hold_expires_at, waiting for payment
    CONFIRMED = "confirmed"  # paid
    CANCELLED = "cancelled"
    EXPIRED = "expired"  # hold ran out before payment


class Operator(Base):
    __tablename__ = "operators"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(120))
    # Only the SHA-256 of the API key is stored; the key itself is shown once at creation.
    api_key_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Activity(Base):
    __tablename__ = "activities"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    operator_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("operators.id"), index=True)
    name: Mapped[str] = mapped_column(String(160))
    description: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Slot(Base):
    __tablename__ = "slots"
    __table_args__ = (
        CheckConstraint("capacity > 0", name="slot_capacity_positive"),
        CheckConstraint("price_idr >= 0", name="slot_price_non_negative"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    activity_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("activities.id"), index=True)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    capacity: Mapped[int] = mapped_column(Integer)
    price_idr: Mapped[int] = mapped_column(Integer)  # per person, in whole rupiah


class Booking(Base):
    __tablename__ = "bookings"
    __table_args__ = (
        CheckConstraint("party_size > 0", name="booking_party_positive"),
        Index("ix_bookings_slot_status", "slot_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    slot_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("slots.id"))
    customer_name: Mapped[str] = mapped_column(String(160))
    customer_whatsapp: Mapped[str] = mapped_column(String(32))
    party_size: Mapped[int] = mapped_column(Integer)
    total_idr: Mapped[int] = mapped_column(Integer)
    status: Mapped[BookingStatus] = mapped_column(
        Enum(BookingStatus, name="booking_status", values_callable=lambda e: [m.value for m in e]),
        default=BookingStatus.PENDING,
    )
    hold_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # True when money was received (or may have been) but the seats are not ours to give.
    refund_due: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PaymentEvent(Base):
    """Every webhook event we have processed. The primary key makes processing idempotent."""

    __tablename__ = "payment_events"

    event_id: Mapped[str] = mapped_column(String(120), primary_key=True)
    booking_id: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    status: Mapped[str] = mapped_column(String(32))
    amount_idr: Mapped[int] = mapped_column(Integer, default=0)
    outcome: Mapped[str] = mapped_column(String(32), default="")
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
