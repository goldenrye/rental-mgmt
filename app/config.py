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
