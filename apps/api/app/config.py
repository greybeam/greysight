from typing import Literal

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_STORAGE_PRICE_USD_PER_TB_MONTH = 23.0


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="", extra="ignore")

    data_source: Literal["demo", "snowflake"] = "demo"
    auth_required: bool = False
    supabase_url: str = Field(default="", validation_alias=AliasChoices("SUPABASE_URL"))
    supabase_anon_key: str = Field(
        default="", validation_alias=AliasChoices("SUPABASE_ANON_KEY")
    )
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
    storage_price_usd_per_tb_month: float = Field(
        default=DEFAULT_STORAGE_PRICE_USD_PER_TB_MONTH,
        ge=0,
        validation_alias=AliasChoices("STORAGE_PRICE_USD_PER_TB_MONTH"),
    )
    cors_allowed_origins: tuple[str, ...] = Field(
        default=("http://localhost:3000",),
        validation_alias=AliasChoices("GREYSIGHT_CORS_ALLOWED_ORIGINS"),
    )

    @field_validator("storage_price_usd_per_tb_month", mode="before")
    @classmethod
    def default_empty_storage_price(cls, value: object) -> object:
        if value == "":
            return DEFAULT_STORAGE_PRICE_USD_PER_TB_MONTH
        return value
