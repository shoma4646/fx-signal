from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class SignalConfig(BaseSettings):
    pair: str = "USDJPY=X"
    interval: str = "1h"
    lookback_days: int = 60

    ema_short: int = 9
    ema_long: int = 21
    rsi_period: int = 14
    rsi_buy_threshold: float = 55.0
    rsi_sell_threshold: float = 45.0
    adx_period: int = 14
    adx_threshold: float = 20.0


class SchedulerConfig(BaseSettings):
    interval_minutes: int = 60


class LineConfig(BaseSettings):
    token: str = ""

    model_config = SettingsConfigDict(env_prefix="LINE_")


class Config(BaseSettings):
    signal: SignalConfig = Field(default_factory=SignalConfig)
    scheduler: SchedulerConfig = Field(default_factory=SchedulerConfig)
    line: LineConfig = Field(default_factory=LineConfig)

    model_config = SettingsConfigDict(
        env_file=".env",
        env_nested_delimiter="__",
        extra="ignore",
    )
