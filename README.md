# Booking API

Booking and payment backend for small activity businesses (surf schools, dive shops, tours,
cooking classes). Customers hold seats on a time slot, pay, and the booking is confirmed by a
signed payment webhook. Built with FastAPI, SQLAlchemy 2 (async) and PostgreSQL.

The point of this project is the parts that go wrong in real booking systems, handled and tested:

- **Two people booking the last seat at the same moment** must not both succeed.
- **Payment providers deliver webhooks twice, out of order, or late.** None of that may double-confirm,
  lose a payment or hand out a seat that is gone.
- **Unpaid holds must give their seats back.**
- **Money that does not match the booking** (partial, double, late, or after a cancellation) must be
  recorded exactly, not guessed at.

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
- Money is tracked exactly. `paid_idr` is the running total received for a booking, and a booking is
  confirmed once that covers its total. `refund_due_idr` says how much has to go back to the customer.
  Every event is also stored with its amount and outcome as an audit trail.

| Situation | Result |
| --- | --- |
| Paid in time, covers the total | `confirmed` |
| Paid less than the total | `partial`: the hold stays, the rest can arrive later |
| Paid more than the total | `confirmed`, the excess is `refund_due_idr` |
| Paid again under a new event id | `overpaid`, all of it is `refund_due_idr` |
| Paid after the hold lapsed, seat still free | `late_confirmed` |
| Paid after the hold lapsed, seat taken | booking stays `expired`, everything paid is `refund_due_idr` |
| Paid after the customer cancelled | `refund_due`, everything paid is `refund_due_idr` |
| Provider reports a failed payment | pending booking is cancelled and its seats freed, unless money already arrived |
| Same event id again | `duplicate`, nothing changes |

### Holds

An unpaid booking holds its seats for `HOLD_MINUTES`. Capacity checks ignore lapsed holds, so seats
free up exactly on time. A background task also flips lapsed bookings to `expired` so statuses stay
honest; it is safe to run from several processes at once.

### Retries and access

- **Retry-safe bookings.** Send an `Idempotency-Key` header and repeating the request returns the same
  booking (status 200) instead of holding seats twice. Concurrent retries are serialised by the slot
  lock, and the key is scoped to the slot. Reusing a key with a different body is rejected.
- **Booking tokens.** Creating a booking returns an `access_token`. Reading, cancelling or paying for a
  booking needs it as `X-Booking-Token`; a wrong token and an unknown id both answer `404`. The token is
  an HMAC of the booking id (not stored), so a retry can be handed the same token again.
- **Production guard.** With `ENVIRONMENT=production` the app refuses to start on the default secrets.

### Notifications (outbox)

Booking changes write a message to an outbox table in the same transaction, so a message exists exactly
when the change was committed. A background worker delivers them with `SELECT … FOR UPDATE SKIP LOCKED`,
so several workers can run without sending a message twice at the same time. Delivery is at least once,
failed sends are retried up to `OUTBOX_MAX_ATTEMPTS`, and the notifier gets a stable `message_id` to
deduplicate. Only a console notifier exists so far; see [docs/mvp.md](docs/mvp.md).

## API

| Method | Path | Auth | Purpose |
| --- | --- | --- | --- |
| POST | `/activities` | operator key | create an activity |
| GET | `/activities` | none | list activities |
| POST | `/activities/{id}/slots` | operator key | add a time slot (capacity, price per person in IDR) |
| GET | `/activities/{id}/slots` | none | upcoming slots with seats still open |
| POST | `/slots/{id}/bookings` | none | hold seats; optional `Idempotency-Key`; returns the booking token |
| GET | `/bookings/{id}` | booking token | booking status, `paid_idr`, `refund_due_idr` |
| POST | `/bookings/{id}/cancel` | booking token | cancel; anything already paid becomes refund due |
| POST | `/bookings/{id}/pay` | booking token | create or return the payment link (**placeholder provider**) |
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
# create an activity and a slot with that key (see /docs), book it (keep the access_token),
# then pay for it the way a provider would:
uv run python -m app.cli simulate-payment <booking-id> --amount 900000 --event-id evt_1
uv run python -m app.cli simulate-payment <booking-id> --amount 900000 --event-id evt_1   # duplicate
# watch the server log: the outbox "sends" each message through the console notifier
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
- **Postgres-specific.** Idempotency uses `INSERT … ON CONFLICT DO NOTHING`, the outbox uses `SKIP LOCKED`.
- **Outbox sends inside its transaction.** Simple and correct for a small volume; a high-volume system
  would claim rows, commit, send, then mark them. The notifier already receives a `message_id` for that.
- **Tokens are derived, not stored.** Rotating `TOKEN_SECRET` invalidates every booking token.
- **Placeholders are explicit.** The payment provider and WhatsApp notifier are interfaces with fakes
  behind them, and unimplemented choices fail loudly. What is built and what is not is in
  [docs/mvp.md](docs/mvp.md).
