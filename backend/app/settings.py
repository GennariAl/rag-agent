from typing import Annotated

from pydantic import field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    stage: str | None = None
    oidc_client_id: str | None = None
    oidc_issuer_url: str | None = None
    cors_origins: Annotated[list[str], NoDecode] = []

    local_user_groups: Annotated[list[str], NoDecode] = []

    @field_validator("cors_origins", mode="before")
    @classmethod
    def decode_cors_origins(cls, values: str | None) -> list[str]:
        if not values:
            return []
        return [v.strip() for v in values.split(",")]

    @field_validator("local_user_groups", mode="before")
    @classmethod
    def decode_local_user_groups(cls, values: str | None) -> list[str]:
        if not values:
            return []
        return [v.strip() for v in values.split(",")]

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )
