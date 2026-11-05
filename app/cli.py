"""Small admin and demo helpers.

uv run python -m app.cli create-operator "Canggu Surf School"
uv run python -m app.cli simulate-payment <booking-id> --amount 450000
"""

import argparse
import asyncio
import json
import urllib.error
import urllib.request
import uuid

from app.config import settings
from app.db import SessionLocal, engine
from app.models import Operator
from app.security import new_api_key, sign_webhook


async def create_operator(name: str) -> None:
    key, key_hash = new_api_key()
    async with SessionLocal() as session, session.begin():
        session.add(Operator(name=name, api_key_hash=key_hash))
    await engine.dispose()
    print(f"Operator created: {name}")
    print(f"API key (shown once, send it as the X-API-Key header): {key}")


def simulate_payment(
    booking_id: str, amount: int, status: str, event_id: str, base_url: str
) -> None:
    """Send a signed payment event to a running server, like the payment provider would."""
    body = json.dumps(
        {"event_id": event_id, "booking_id": booking_id, "status": status, "amount_idr": amount}
    ).encode()
    request = urllib.request.Request(
        f"{base_url}/webhooks/payments",
        data=body,
        headers={
            "Content-Type": "application/json",
            "X-Signature": sign_webhook(body, settings.webhook_secret),
        },
    )
    try:
        with urllib.request.urlopen(request) as response:
            print(response.status, response.read().decode())
    except urllib.error.HTTPError as e:
        print(e.code, e.read().decode())


def main() -> None:
    parser = argparse.ArgumentParser(prog="app.cli")
    sub = parser.add_subparsers(dest="command", required=True)

    op = sub.add_parser("create-operator", help="create an operator and print its API key")
    op.add_argument("name")

    pay = sub.add_parser("simulate-payment", help="send a signed payment webhook")
    pay.add_argument("booking_id")
    pay.add_argument("--amount", type=int, required=True)
    pay.add_argument("--status", choices=["paid", "failed"], default="paid")
    pay.add_argument("--event-id", default=None, help="repeat an id to test idempotency")
    pay.add_argument("--base-url", default="http://127.0.0.1:8000")

    args = parser.parse_args()
    if args.command == "create-operator":
        asyncio.run(create_operator(args.name))
    else:
        simulate_payment(
            args.booking_id,
            args.amount,
            args.status,
            args.event_id or f"evt_{uuid.uuid4().hex[:12]}",
            args.base_url,
        )


if __name__ == "__main__":
    main()
