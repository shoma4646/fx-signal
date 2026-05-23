from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class SignalConfig(BaseSettings):
    pair: str = "USDJPY=X"
    interval: str = "1h"
    lookback_days: int = 365          # A-1: 統計的信頼性のため1年分に延長

    ema_short: int = 5                # A-3: データ検証済み（旧9）
    ema_long: int = 20                # A-3: データ検証済み（旧21）
    rsi_period: int = 14
    rsi_buy_threshold: float = 55.0
    rsi_sell_threshold: float = 45.0
    adx_period: int = 14
    adx_threshold: float = 20.0

    spread_pips: float = 0.3          # A-4: スプレッドコスト（往復）
    session_filter: bool = True       # A-2: 低ボラ時間帯フィルター


class SchedulerConfig(BaseSettings):
    interval_minutes: int = 60


class Config(BaseSettings):
    signal: SignalConfig = Field(default_factory=SignalConfig)
    scheduler: SchedulerConfig = Field(default_factory=SchedulerConfig)

    model_config = SettingsConfigDict(
        env_file=".env",
        env_nested_delimiter="__",
        extra="ignore",
    )
