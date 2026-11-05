from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://localhost/booking_api"
    # Shared secret the payment provider uses to sign webhook bodies.
    webhook_secret: str = "dev-webhook-secret"
    # How long an unpaid booking holds its seats.
    hold_minutes: int = 15
    # How often the background task marks lapsed holds as expired.
    expiry_interval_seconds: int = 60


settings = Settings()
