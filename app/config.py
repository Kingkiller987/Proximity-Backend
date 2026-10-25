import os
from typing import Optional
from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    ENV: str = "development"
    DATABASE_URL: Optional[str] = None

    POSTGRES_USER: str = "proximity"
    POSTGRES_PASSWORD: str = ""
    POSTGRES_HOST: str = "127.0.0.1"
    POSTGRES_PORT: int = 5432
    POSTGRES_DB: str = "proximity"
    POSTGRES_TEST_DB: str = "proximity_test"

    SECRET_KEY: str = "proximity-insecure-secret-key-for-local-dev-change-in-prod"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 15
    REFRESH_TOKEN_EXPIRE_DAYS: int = 30

    MEDIA_STORAGE_DIR: str = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "storage", "media"
    )
    MAX_IMAGE_BYTES: int = 5 * 1024 * 1024  # 5 MB
    MAX_LOCATION_AGE_SECONDS: int = 30
    MAX_GPS_ACCURACY_METERS: float = 50.0

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    def get_database_url(self, is_test: bool = False) -> str:
        if is_test:
            return f"postgresql+psycopg://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_TEST_DB}"
        if self.DATABASE_URL:
            url = self.DATABASE_URL
            if url.startswith("postgresql://"):
                url = url.replace("postgresql://", "postgresql+psycopg://", 1)
            elif url.startswith("postgres://"):
                url = url.replace("postgres://", "postgresql+psycopg://", 1)
            return url
        return f"postgresql+psycopg://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"

    def safe_repr(self) -> dict:
        data = self.model_dump()
        if "POSTGRES_PASSWORD" in data:
            data["POSTGRES_PASSWORD"] = "[REDACTED]"
        if "SECRET_KEY" in data:
            data["SECRET_KEY"] = "[REDACTED]"
        if "DATABASE_URL" in data and data["DATABASE_URL"]:
            data["DATABASE_URL"] = "[REDACTED]"
        return data


@lru_cache
def get_settings() -> Settings:
    return Settings()
