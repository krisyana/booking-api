# Booking API

Booking and payment backend for small activity businesses (surf schools, dive shops, tours,
cooking classes). Customers hold seats on a time slot, pay, and the booking is confirmed by a
signed payment webhook. Built with FastAPI, SQLAlchemy 2 (async) and PostgreSQL.

The point of this project is the parts that go wrong in real booking systems, handled and tested:

- **Two people booking the last seat at the same moment** must not both succeed.
- **Payment providers deliver webhooks twice, out of order, or late.** None of that may double-confirm,
  lose a payment or hand out a seat that is gone.
- **Unpaid holds must give their seats back.**

## How it works

```
customer ──POST /slots/{id}/bookings──▶ hold seats (pending, 15 min)
provider ──POST /webhooks/payments────▶ verify signature ▶ record event ▶ confirm / flag refund
background task ───────────────────────▶ mark lapsed holds as expired
```

### No double-booking

Every change to a slot's seat count first takes a row lock on that slot
(`SELECT … FOR UPDATE`). Concurrent requests for one slot run one after another, so the
"are there enough seats?" check can never be raced. Locks are always taken in the same order
(slot, then booking) so they cannot deadlock. Different slots do not block each other.

`tests/test_concurrency.py` fires 20 simultaneous requests at a 5-seat slot and expects exactly 5
to succeed. I checked that this test is meaningful: with the lock removed it fails (one run
accepted 27 seats on a 10-seat slot).

### Idempotent payment webhooks

- The signature (`X-Signature: sha256=<hmac of the raw body>`) is checked before the body is parsed.
- Each event id is stored in `payment_events` (primary key) *before* anything else happens. A repeat
  hits the conflict and changes nothing, even when ten copies arrive at once (tested).
- Edge cases are decided explicitly instead of failing silently:

| Situation | Result |
| --- | --- |
| Paid in time, right amount | `confirmed` |
| Paid after the hold lapsed, seat still free | `late_confirmed` |
| Paid after the hold lapsed, seat taken | booking stays `expired`, `refund_due = true` |
| Paid after the customer cancelled | `refund_due = true` |
| Paid amount differs from the total | not confirmed, `refund_due = true` |
| Provider reports a failed payment | pending booking is cancelled, seats freed |
| Same event id again | `duplicate`, nothing changes |

Each processed event is stored with its amount and outcome, so refunds can be worked out from
`payment_events`.

### Holds

An unpaid booking holds its seats for `HOLD_MINUTES`. Capacity checks ignore lapsed holds, so seats
free up exactly on time. A background task also flips lapsed bookings to `expired` so statuses stay
honest; it is safe to run from several processes at once.

## API

| Method | Path | Auth | Purpose |
| --- | --- | --- | --- |
| POST | `/activities` | operator key | create an activity |
| GET | `/activities` | none | list activities |
| POST | `/activities/{id}/slots` | operator key | add a time slot (capacity, price per person in IDR) |
| GET | `/activities/{id}/slots` | none | upcoming slots with seats still open |
| POST | `/slots/{id}/bookings` | none | hold seats |
| GET | `/bookings/{id}` | none (unguessable UUID) | booking status |
| POST | `/bookings/{id}/cancel` | none | cancel; a paid booking is flagged for refund |
| GET | `/slots/{id}/bookings` | operator key | roster for one of your slots |
| POST | `/webhooks/payments` | HMAC signature | payment events |

Operators authenticate with an `X-API-Key` header. Only a SHA-256 hash of the key is stored. Operators
can only see and change their own activities. Interactive docs are at `/docs`.

## Run it

Requires Python 3.13+, [uv](https://docs.astral.sh/uv/) and PostgreSQL.

```bash
createdb booking_api
uv sync
cp .env.example .env
uv run alembic upgrade head
uv run uvicorn app.main:app --reload
```

Try the whole flow:

```bash
uv run python -m app.cli create-operator "Canggu Surf School"   # prints an API key once
# create an activity and a slot with that key (see /docs), book it, then pay for it like a provider would:
uv run python -m app.cli simulate-payment <booking-id> --amount 900000 --event-id evt_1
uv run python -m app.cli simulate-payment <booking-id> --amount 900000 --event-id evt_1   # duplicate
```

## Tests

```bash
createdb booking_api_test
uv run pytest
```

The tests run against real PostgreSQL, because the locking behaviour is the thing being tested; SQLite
would not exercise it. CI runs lint, a migration up/down/up check and the full suite on every push.

## Decisions and limits

- **Row locks, not retries.** An optimistic version column would need retry loops under load; one short
  lock per slot is simpler and the contention is naturally per slot.
- **Postgres-specific.** Idempotency uses `INSERT … ON CONFLICT DO NOTHING`.
- **No real payment provider yet.** The webhook defines the contract (signed JSON event). Adapting it to
  a specific provider such as Xendit or Midtrans means mapping their signature scheme and payload, which
  differ from this one. Creating the payment itself (invoice or QRIS link) is not built.
- **Refunds are flagged, not executed.** `refund_due` plus the stored amounts tell an operator what to return.
- **Not built yet:** rate limiting, WhatsApp or email notifications, operator self-sign-up, time-zone
  display (times are stored in UTC).
