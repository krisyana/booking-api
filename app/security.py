import base64
import hashlib
import hmac
import secrets
import uuid


def hash_api_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def new_api_key() -> tuple[str, str]:
    """Return (key to show the operator once, hash to store)."""
    key = "bk_" + secrets.token_urlsafe(32)
    return key, hash_api_key(key)


def sign_webhook(body: bytes, secret: str) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def verify_webhook(body: bytes, header: str | None, secret: str) -> bool:
    return hmac.compare_digest(sign_webhook(body, secret), header or "")


def booking_token(booking_id: uuid.UUID, secret: str) -> str:
    """Customer access token for one booking.

    Derived from the booking id with an HMAC instead of stored, so a client that retries a
    booking request (and never saw the first response) can be given the same token again.
    """
    digest = hmac.new(secret.encode(), str(booking_id).encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")


def verify_booking_token(booking_id: uuid.UUID, token: str | None, secret: str) -> bool:
    expected = booking_token(booking_id, secret).encode()
    return hmac.compare_digest(expected, (token or "").encode())
