import uuid

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import func, select

from app.deps import OperatorDep, SessionDep
from app.models import Activity, Booking, Slot, utcnow
from app.schemas import ActivityIn, ActivityOut, SlotIn, SlotOut
from app.services.bookings import counts_toward_capacity

router = APIRouter(prefix="/activities", tags=["activities"])


@router.post("", response_model=ActivityOut, status_code=status.HTTP_201_CREATED)
async def create_activity(body: ActivityIn, operator: OperatorDep, session: SessionDep):
    activity = Activity(operator_id=operator.id, **body.model_dump())
    session.add(activity)
    await session.commit()  # the auth check already opened a transaction on this session
    return activity


@router.get("", response_model=list[ActivityOut])
async def list_activities(session: SessionDep):
    result = await session.scalars(select(Activity).order_by(Activity.name))
    return list(result)


@router.get("/{activity_id}", response_model=ActivityOut)
async def get_activity(activity_id: uuid.UUID, session: SessionDep):
    activity = await session.get(Activity, activity_id)
    if activity is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "activity not found")
    return activity


@router.post("/{activity_id}/slots", response_model=SlotOut, status_code=status.HTTP_201_CREATED)
async def create_slot(
    activity_id: uuid.UUID, body: SlotIn, operator: OperatorDep, session: SessionDep
):
    activity = await session.get(Activity, activity_id)
    # Same answer for "does not exist" and "belongs to someone else".
    if activity is None or activity.operator_id != operator.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "activity not found")
    slot = Slot(activity_id=activity.id, **body.model_dump())
    session.add(slot)
    await session.commit()
    return SlotOut(
        **{c: getattr(slot, c) for c in SlotOut.model_fields if c != "seats_available"},
        seats_available=slot.capacity,
    )


@router.get("/{activity_id}/slots", response_model=list[SlotOut])
async def list_slots(activity_id: uuid.UUID, session: SessionDep):
    """Upcoming slots with the seats still open right now."""
    now = utcnow()
    taken = (
        select(Booking.slot_id, func.sum(Booking.party_size).label("taken"))
        .where(counts_toward_capacity(now))
        .group_by(Booking.slot_id)
        .subquery()
    )
    rows = await session.execute(
        select(Slot, func.coalesce(taken.c.taken, 0))
        .outerjoin(taken, taken.c.slot_id == Slot.id)
        .where(Slot.activity_id == activity_id, Slot.starts_at > now)
        .order_by(Slot.starts_at)
    )
    return [
        SlotOut(
            id=slot.id,
            activity_id=slot.activity_id,
            starts_at=slot.starts_at,
            capacity=slot.capacity,
            price_idr=slot.price_idr,
            seats_available=max(slot.capacity - int(count), 0),
        )
        for slot, count in rows
    ]
