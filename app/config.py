from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import SecretStr


class Settings(BaseSettings):
    app_name: str = "Rental Management API"
    database_url: str = "sqlite:///./data/rental_mgmt.db"
    jwt_secret_key: str = "change-me-in-production"
    jwt_algorithm: str = "HS256"
    data_encryption_master_key: SecretStr = SecretStr("")
    data_encryption_key_file: str = str(Path(__file__).resolve().parent.parent / ".secrets" / "data-encryption.key")
    access_token_expire_minutes: int = 60 * 24
    super_admin_initial_password: str = ""
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    # Read the client IP from nginx's X-Real-IP; enable only when the API port is not publicly reachable.
    trust_proxy_headers: bool = False
    # Off: landlords sign up only with an admin invitation and tenants cannot self-register.
    # On (local development only): anyone can register an active landlord or tenant account.
    open_registration: bool = False
    google_calendar_client_id: str = ""
    google_calendar_client_secret: SecretStr = SecretStr("")
    google_calendar_redirect_uri: str = ""
    # Used only for links inside exported events, never inferred from request headers.
    google_calendar_app_url: str = ""
    # Web push reminders: base64url raw P-256 key from `python -m scripts.generate_vapid_key`.
    web_push_vapid_private_key: SecretStr = SecretStr("")
    # mailto: or https: contact sent to push services in the VAPID "sub" claim.
    web_push_contact: str = ""
    # iPhone app reminders through Apple Push Notification service (token-based .p8 auth key).
    # All of team ID, key ID, topic and a key (inline PEM or file path) are required.
    apns_team_id: str = ""
    apns_key_id: str = ""
    # Inline .p8 PEM; literal "\n" sequences are accepted for single-line env values.
    apns_auth_key: SecretStr = SecretStr("")
    apns_auth_key_file: str = ""
    # The iOS bundle identifier, e.g. com.jpi.rental-mgmt.
    apns_topic: str = ""
    # Password reset emails. Empty SMTP_HOST or SMTP_FROM keeps "Forgot password" unavailable.
    smtp_host: str = ""
    # 465 uses implicit TLS; any other port uses STARTTLS unless smtp_starttls is false.
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: SecretStr = SecretStr("")
    smtp_starttls: bool = True
    smtp_from: str = ""
    # Public website URL for links in emails; falls back to google_calendar_app_url.
    app_url: str = ""
    # Stable website hostname and exact HTTPS origins. Empty keeps passkeys unavailable.
    passkey_rp_id: str = ""
    passkey_origins: str = ""
    # Comma-separated Apple application identifiers (App ID prefix + bundle ID).
    passkey_apple_app_ids: str = ""

    @property
    def allowed_origins(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parent.parent / ".env",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
