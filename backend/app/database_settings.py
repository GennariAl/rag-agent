import urllib.parse as urlparse
from typing import Annotated

from pydantic import Field, PostgresDsn, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict


class DatabaseSettings(BaseSettings):
    pg_database: Annotated[str, Field(alias="pgdatabase")]
    pg_host: Annotated[str, Field(alias="pghost")]
    pg_user: Annotated[str, Field(alias="pguser")]
    pg_password: Annotated[str | None, Field(alias="pgpassword")]

    @computed_field
    @property
    def database_uri(self) -> PostgresDsn:
        return PostgresDsn.build(
            scheme="postgresql+asyncpg",
            username=self.pg_user,
            password=urlparse.quote(self.pg_password) if self.pg_password else None,
            host=self.pg_host,
            port=5432,
            path=self.pg_database,
        )

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )
