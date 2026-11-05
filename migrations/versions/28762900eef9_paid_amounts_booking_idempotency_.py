"""paid amounts, booking idempotency, payment fields, outbox

Replaces the boolean bookings.refund_due with bookings.paid_idr (refund_due_idr is computed from
it), adds the idempotency key and payment reference columns, and creates the notification outbox.

Revision ID: 28762900eef9
Revises: 89bb06cd853b
Create Date: 2026-11-05 12:42:42.211475

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "28762900eef9"
down_revision: str | Sequence[str] | None = "89bb06cd853b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "outbox_messages",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=40), nullable=False),
        sa.Column("recipient", sa.String(length=32), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_outbox_unsent",
        "outbox_messages",
        ["created_at"],
        unique=False,
        postgresql_where=sa.text("sent_at IS NULL"),
    )

    op.add_column(
        "bookings", sa.Column("paid_idr", sa.Integer(), nullable=False, server_default="0")
    )
    # Existing bookings: what was received is the sum of their paid events.
    op.execute(
        """
        UPDATE bookings SET paid_idr = COALESCE(
            (SELECT SUM(e.amount_idr) FROM payment_events e
             WHERE e.booking_id = bookings.id AND e.status = 'paid'), 0)
        """
    )
    op.alter_column("bookings", "paid_idr", server_default=None)

    op.add_column("bookings", sa.Column("idempotency_key", sa.String(length=120), nullable=True))
    op.add_column("bookings", sa.Column("payment_reference", sa.String(length=120), nullable=True))
    op.add_column("bookings", sa.Column("payment_url", sa.String(length=500), nullable=True))
    op.create_unique_constraint(
        "uq_booking_idempotency", "bookings", ["slot_id", "idempotency_key"]
    )
    op.drop_column("bookings", "refund_due")


def downgrade() -> None:
    op.add_column(
        "bookings",
        sa.Column("refund_due", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    # Best effort: the flag was true when money was held that the customer should get back.
    op.execute(
        """
        UPDATE bookings SET refund_due = (
            (status IN ('cancelled', 'expired') AND paid_idr > 0)
            OR (status = 'confirmed' AND paid_idr > total_idr))
        """
    )
    op.alter_column("bookings", "refund_due", server_default=None)

    op.drop_constraint("uq_booking_idempotency", "bookings", type_="unique")
    op.drop_column("bookings", "payment_url")
    op.drop_column("bookings", "payment_reference")
    op.drop_column("bookings", "idempotency_key")
    op.drop_column("bookings", "paid_idr")
    op.drop_index(
        "ix_outbox_unsent",
        table_name="outbox_messages",
        postgresql_where=sa.text("sent_at IS NULL"),
    )
    op.drop_table("outbox_messages")
