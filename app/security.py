import hashlib
import hmac
import secrets


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
