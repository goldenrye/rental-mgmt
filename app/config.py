from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Rental Management API"
    database_url: str = "sqlite:///./data/rental_mgmt.db"
    jwt_secret_key: str = "change-me-in-production"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 60 * 24
    super_admin_initial_password: str = ""
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    # Read the client IP from nginx's X-Real-IP; enable only when the API port is not publicly reachable.
    trust_proxy_headers: bool = False
    # New landlord sign-ups stay inactive until the super admin activates them; tenants cannot self-register.
    registration_requires_approval: bool = True

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
