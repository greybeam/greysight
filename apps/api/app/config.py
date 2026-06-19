from typing import Literal

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_STORAGE_PRICE_USD_PER_TB_MONTH = 23.0
DEFAULT_ESTIMATED_CREDIT_PRICE_USD = 3.0


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="", extra="ignore", populate_by_name=True
    )

    data_source: Literal["demo", "snowflake"] = "demo"
    auth_required: bool = False
    supabase_url: str = Field(
        default="",
        validation_alias=AliasChoices("SUPABASE_URL", "NEXT_PUBLIC_SUPABASE_URL"),
    )
    supabase_anon_key: str = Field(
        default="",
        validation_alias=AliasChoices(
            "SUPABASE_ANON_KEY", "NEXT_PUBLIC_SUPABASE_ANON_KEY"
        ),
    )
    supabase_service_role_key: str = Field(
        default="", validation_alias=AliasChoices("SUPABASE_SERVICE_ROLE_KEY")
    )
    default_window_days: int = Field(
        default=30,
        gt=0,
        le=365,
        validation_alias=AliasChoices("GREYSIGHT_DEFAULT_WINDOW_DAYS"),
    )
    query_timeout_seconds: int = Field(
        default=180,
        gt=0,
        validation_alias=AliasChoices("GREYSIGHT_QUERY_TIMEOUT_SECONDS"),
    )
    storage_price_usd_per_tb_month: float = Field(
        default=DEFAULT_STORAGE_PRICE_USD_PER_TB_MONTH,
        ge=0,
        validation_alias=AliasChoices("STORAGE_PRICE_USD_PER_TB_MONTH"),
    )
    estimated_credit_price_usd: float = Field(
        default=DEFAULT_ESTIMATED_CREDIT_PRICE_USD,
        gt=0,
        validation_alias=AliasChoices("ESTIMATED_CREDIT_PRICE_USD"),
    )
    cors_allowed_origins: tuple[str, ...] = Field(
        default=("http://localhost:3000",),
        validation_alias=AliasChoices("GREYSIGHT_CORS_ALLOWED_ORIGINS"),
    )
    query_concurrency: int = Field(
        default=8, gt=0, le=64,
        validation_alias=AliasChoices("GREYSIGHT_QUERY_CONCURRENCY"),
    )

    @field_validator(
        "storage_price_usd_per_tb_month",
        "estimated_credit_price_usd",
        mode="before",
    )
    @classmethod
    def default_empty_price(cls, value: object, info: object) -> object:
        if value == "":
            if getattr(info, "field_name", "") == "estimated_credit_price_usd":
                return DEFAULT_ESTIMATED_CREDIT_PRICE_USD
            return DEFAULT_STORAGE_PRICE_USD_PER_TB_MONTH
        return value
