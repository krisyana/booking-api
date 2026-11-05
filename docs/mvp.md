# MVP status

What is built, what is a clearly marked placeholder, and what is not started. A placeholder is a
real interface with a fake implementation behind it, so the rest of the app, the tests and the
data model already work with it; swapping in the real thing touches one file.

## Built and tested

| Capability | Where |
| --- | --- |
| Capacity-safe booking (row lock per slot) | `app/services/bookings.py` |
| Hold expiry, including late payments | `app/services/bookings.py`, `app/services/payments.py` |
| Idempotent, signed payment webhooks | `app/services/payments.py`, `app/security.py` |
| Money model: running `paid_idr`, partial payments, overpayments, `refund_due_idr` | `app/models.py`, `app/services/payments.py` |
| Retry-safe booking creation (`Idempotency-Key`) | `app/services/bookings.py` |
| Customer booking tokens (read, cancel, pay) | `app/deps.py`, `app/security.py` |
| Operator API keys and ownership checks | `app/deps.py`, `app/routers/` |
| Transactional outbox with competing workers (`SKIP LOCKED`) | `app/services/outbox.py` |
| Background jobs that survive a failed run | `app/background.py` |
| Production refuses default secrets | `app/config.py` |
| CI: lint, migration up/down/up, tests on real PostgreSQL | `.github/workflows/ci.yml` |

## Placeholders

### Payment provider  (`app/integrations/payments.py`)

- **Exists:** the `PaymentProvider` interface, a `FakeProvider`, and `POST /bookings/{id}/pay`, which
  creates one charge per booking (safe against double taps) for the amount still unpaid.
  `PAYMENT_PROVIDER=xendit` or `midtrans` is accepted by config but answers `501` until implemented.
- **A real adapter needs to:**
  1. Implement `create_charge` against the provider's API (invoice or QRIS link) and store its reference.
  2. Map the provider's webhook to `PaymentEventIn`: its event id, the booking id (pass it as the
     provider's external reference), and the amount in whole rupiah.
  3. Verify the provider's own callback authentication. This project verifies an HMAC of the body;
     providers differ, so make `verify_webhook` pluggable per provider.
  4. Run the provider's sandbox through `tests/test_webhooks.py`-style cases (duplicates, late, partial).
- **Done when:** a sandbox payment confirms a booking end to end with no manual step.

### Notifications  (`app/integrations/notifications.py`)

- **Exists:** the `Notifier` interface, a `ConsoleNotifier` that logs, the outbox that feeds it, and the
  list of messages in `TEMPLATES` (`booking_held`, `booking_confirmed`, `booking_cancelled`, `refund_due`).
  `NOTIFIER=whatsapp` fails at startup until implemented.
- **A real adapter needs to:**
  1. Implement `send` for a WhatsApp provider. Check the current WhatsApp Business rules: messages
     sent first by a business generally need pre-approved templates and the customer's opt-in.
  2. Submit the `TEMPLATES` texts for approval (in English and Indonesian).
  3. Use `message_id` to deduplicate: the outbox delivers at least once.
- **Done when:** a real booking produces a WhatsApp message and a failed send is retried, then given
  up after `OUTBOX_MAX_ATTEMPTS`.

## Not started

| Item | Notes |
| --- | --- |
| Rate limiting | Booking creation per IP and per phone number; needs shared state (Redis) once there is more than one process |
| Operator and customer UI | A Next.js front end over this API |
| Operator sign-up | Operators are created from the CLI today |
| Refund execution | `refund_due_idr` says what is owed; sending the money back is manual |
| Recurring slots | Weekly schedules instead of creating each slot by hand |
| Waitlist | Offer freed seats to the next customer |
| Observability | Structured logs, metrics, alerts on webhook failures and a growing outbox |
| Time zones | Times are stored in UTC and returned as sent; nothing localises them |
