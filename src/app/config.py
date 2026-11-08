from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    service_name: str = "mvp-oficina-ordens"
    service_version: str = "0.1.0"
    environment: Literal["dev", "test", "prod"] = "dev"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    database_url: str = ""
    jwt_secret_key: str = "unsafe"
    jwt_algorithm: Literal["HS256"] = "HS256"
    jwt_access_token_expire_minutes: int = 30
    jwt_internal_token_expire_minutes: int = 5
    jwt_issuer: str = "mvp-oficina-ordens"
    jwt_user_audience: str = "mvp-oficina-users"
    service_clients_json: str = "{}"
    internal_auth_url: str = "http://ordens:8000/internal/v1/auth/token"
    resources_base_url: str = "http://recursos:8000"
    finance_base_url: str = "http://financeiro:8000"
    service_client_id: str = "mvp-oficina-ordens"
    service_client_secret: str = ""
    internal_connect_timeout_seconds: float = 1.0
    internal_read_timeout_seconds: float = 2.0

    model_config = SettingsConfigDict(env_file=None, extra="ignore")

    def validate_runtime(self) -> None:
        if not self.database_url:
            raise ValueError("DATABASE_URL must be provided at runtime")
        if self.environment == "prod" and self.jwt_secret_key == "unsafe":
            raise ValueError("JWT_SECRET_KEY must be provided in production")


@lru_cache
def get_settings() -> Settings:
    return Settings()
