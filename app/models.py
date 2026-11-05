import enum
import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
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
        # Retrying a request with the same Idempotency-Key must not create a second booking.
        UniqueConstraint("slot_id", "idempotency_key", name="uq_booking_idempotency"),
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
    # Everything received for this booking, in whole rupiah (events are the audit trail).
    paid_idr: Mapped[int] = mapped_column(Integer, default=0)
    idempotency_key: Mapped[str | None] = mapped_column(String(120), default=None)
    payment_reference: Mapped[str | None] = mapped_column(String(120), default=None)
    payment_url: Mapped[str | None] = mapped_column(String(500), default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    @property
    def refund_due_idr(self) -> int:
        """Money we hold that the customer should get back."""
        if self.status == BookingStatus.CONFIRMED:
            return max(self.paid_idr - self.total_idr, 0)  # overpayment
        if self.status in (BookingStatus.CANCELLED, BookingStatus.EXPIRED):
            return self.paid_idr  # paid, but the seats are not theirs
        return 0  # pending: a partial payment is waiting to be completed


class PaymentEvent(Base):
    """Every webhook event we have processed. The primary key makes processing idempotent."""

    __tablename__ = "payment_events"

    event_id: Mapped[str] = mapped_column(String(120), primary_key=True)
    booking_id: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    status: Mapped[str] = mapped_column(String(32))
    amount_idr: Mapped[int] = mapped_column(Integer, default=0)
    outcome: Mapped[str] = mapped_column(String(32), default="")
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class OutboxMessage(Base):
    """A notification waiting to be sent.

    Written in the same transaction as the change it announces, so a message exists if and
    only if the change was committed. A worker delivers it afterwards (at least once).
    """

    __tablename__ = "outbox_messages"
    __table_args__ = (
        Index("ix_outbox_unsent", "created_at", postgresql_where=text("sent_at IS NULL")),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    kind: Mapped[str] = mapped_column(String(40))
    recipient: Mapped[str] = mapped_column(String(32))
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
