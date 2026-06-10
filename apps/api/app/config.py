from typing import Literal

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="", extra="ignore")

    data_source: Literal["demo", "snowflake"] = "demo"
    auth_required: bool = False
    default_window_days: int = Field(
        default=30,
        gt=0,
        le=365,
        validation_alias=AliasChoices("GREYSIGHT_DEFAULT_WINDOW_DAYS"),
    )
    query_timeout_seconds: int = Field(
        default=60,
        gt=0,
        validation_alias=AliasChoices("GREYSIGHT_QUERY_TIMEOUT_SECONDS"),
    )
