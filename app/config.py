from typing import Literal

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEV_WEBHOOK_SECRET = "dev-webhook-secret"
DEV_TOKEN_SECRET = "dev-token-secret"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    environment: Literal["development", "production"] = "development"
    database_url: str = "postgresql+asyncpg://localhost/booking_api"

    # Shared secret the payment provider uses to sign webhook bodies.
    webhook_secret: str = DEV_WEBHOOK_SECRET
    # Secret that customer booking tokens are derived from. Rotating it invalidates all tokens.
    token_secret: str = DEV_TOKEN_SECRET

    # How long an unpaid booking holds its seats.
    hold_minutes: int = 15
    # How often lapsed holds are marked as expired.
    expiry_interval_seconds: int = 60

    # Outbox: how often pending notifications are delivered, and how often one is retried.
    outbox_interval_seconds: int = 5
    outbox_max_attempts: int = 5

    # MVP placeholders: only "fake" and "console" are implemented. See docs/mvp.md.
    payment_provider: Literal["fake", "xendit", "midtrans"] = "fake"
    notifier: Literal["console", "whatsapp"] = "console"
    public_base_url: str = "http://127.0.0.1:8000"

    @model_validator(mode="after")
    def _refuse_default_secrets_in_production(self) -> "Settings":
        if self.environment == "production":
            weak = [
                name
                for name, value, default in (
                    ("WEBHOOK_SECRET", self.webhook_secret, DEV_WEBHOOK_SECRET),
                    ("TOKEN_SECRET", self.token_secret, DEV_TOKEN_SECRET),
                )
                if value == default
            ]
            if weak:
                raise ValueError(
                    f"{', '.join(weak)} must be changed from the development default "
                    "when ENVIRONMENT=production"
                )
        return self


settings = Settings()
