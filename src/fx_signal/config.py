from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class SignalConfig(BaseSettings):
    pair: str = "USDJPY=X"
    interval: str = "1h"
    lookback_days: int = 365

    # RSI逆張り戦略（バックテストで最良と判明）
    rsi_period: int = 14
    rsi_oversold: float = 30.0        # 買いシグナル閾値
    rsi_overbought: float = 70.0      # 売りシグナル閾値

    # ATRベースのTP/SL
    atr_period: int = 14
    sl_atr_mult: float = 1.5          # 損切り = ATR × 1.5
    tp_atr_mult: float = 2.5          # 利確  = ATR × 2.5 (R:R = 1:1.67)

    spread_pips: float = 0.3
    session_filter: bool = True


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
