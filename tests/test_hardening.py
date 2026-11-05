import asyncio
import uuid

import pytest
from pydantic import ValidationError

from app.background import run_periodically
from app.config import DEV_TOKEN_SECRET, DEV_WEBHOOK_SECRET, Settings, settings
from app.integrations.notifications import get_notifier
from app.integrations.payments import get_payment_provider
from app.security import booking_token, verify_booking_token


def test_booking_tokens_are_stable_and_tied_to_one_booking_and_secret():
    a, b = uuid.uuid4(), uuid.uuid4()

    assert booking_token(a, "s") == booking_token(a, "s")
    assert booking_token(a, "s") != booking_token(b, "s")
    assert booking_token(a, "s") != booking_token(a, "other-secret")
    assert verify_booking_token(a, booking_token(a, "s"), "s")
    assert not verify_booking_token(b, booking_token(a, "s"), "s")
    assert not verify_booking_token(a, None, "s")
    assert not verify_booking_token(a, "", "s")


def test_production_refuses_the_development_secrets():
    with pytest.raises(ValidationError, match="WEBHOOK_SECRET.*TOKEN_SECRET"):
        Settings(environment="production")
    with pytest.raises(ValidationError, match="TOKEN_SECRET"):
        Settings(environment="production", webhook_secret="x" * 32)


def test_production_accepts_real_secrets_and_development_keeps_defaults():
    assert Settings(environment="production", webhook_secret="a" * 32, token_secret="b" * 32)
    dev = Settings(environment="development")
    assert (dev.webhook_secret, dev.token_secret) == (DEV_WEBHOOK_SECRET, DEV_TOKEN_SECRET)


def test_unimplemented_integrations_fail_loudly_instead_of_pretending(monkeypatch):
    monkeypatch.setattr(settings, "payment_provider", "midtrans")
    monkeypatch.setattr(settings, "notifier", "whatsapp")

    with pytest.raises(NotImplementedError, match="midtrans"):
        get_payment_provider()
    with pytest.raises(NotImplementedError, match="whatsapp"):
        get_notifier()


async def test_a_failing_background_job_is_retried_not_abandoned():
    calls = 0

    async def flaky() -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("database blip")

    task = asyncio.create_task(run_periodically("test", 0.01, flaky))
    await asyncio.sleep(0.15)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert calls >= 3  # it kept running after the first run failed


async def test_the_app_refuses_to_start_with_an_unimplemented_payment_provider(monkeypatch):
    from app.main import app, lifespan

    monkeypatch.setattr(settings, "payment_provider", "xendit")

    with pytest.raises(NotImplementedError, match="xendit"):
        async with lifespan(app):
            pass
