from typing import Annotated

from pydantic import Field, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict


class LLMGWSettings(BaseSettings):
    stage: str
    small_model: str = "eu.anthropic.claude-haiku-4-5-20251001-v1:0"
    medium_model: str = "eu.anthropic.claude-sonnet-4-6"
    big_model: str = "eu.anthropic.claude-opus-4-8"
    # Azure OpenAI deployment for the LLM-as-judge (evaluation feature), reached through the
    # gateway's /foundry endpoint. Reuses `api_version` below (same Azure OpenAI surface as the
    # embedding client).
    eval_model: str = "gpt-5.4"
    endpoints: dict[str, str] = {
        "dev": "[censored]",
        "qual": "[censored]",
        "prod": "[censored]",
        "local": "[censored]",
    }
    llm_gw_key: str
    # Azure OpenAI API version for the embedding client. Kept as `api_ver` in the
    # environment for backward compatibility with the existing .env.
    api_version: Annotated[str, Field(alias="api_ver")] = "2024-10-21"

    @computed_field
    @property
    def endpoint(self) -> str:
        return self.endpoints[self.stage]

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )
