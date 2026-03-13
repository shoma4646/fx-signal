"""
設定管理モジュール

pydantic-settingsを使用して、環境変数(.env)とconfig.tomlから
型安全な設定値を読み込む。
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any, Literal

import structlog
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = structlog.get_logger()


class ExchangeConfig(BaseModel):
    """取引所接続設定"""

    name: str = Field(default="bitbank", description="取引所名 (bitbank / bybit)")
    api_key: str = Field(default="", description="取引所APIキー")
    api_secret: str = Field(default="", description="取引所APIシークレット")
    testnet: bool = Field(default=False, description="テストネットモードの有効化 (Bybitのみ)")


class StrategyConfig(BaseModel):
    """戦略設定"""

    name: str = Field(description="戦略名")
    pairs: list[str] = Field(description="対象通貨ペアのリスト")
    params: dict[str, Any] = Field(default_factory=dict, description="戦略固有のパラメータ")


class SafetyConfig(BaseModel):
    """安全機構の設定

    損失制限やリスク管理に関するパラメータを管理する。
    """

    daily_loss_limit_pct: float = Field(
        default=5.0,
        description="日次損失上限（口座残高に対する%）",
    )
    max_drawdown_pct: float = Field(
        default=20.0,
        description="最大ドローダウン（%）。超過時に自動停止",
    )
    risk_per_trade_pct: float = Field(
        default=2.0,
        description="1トレードあたりのリスク（口座残高に対する%）",
    )
    volatility_pause_atr_multiplier: float = Field(
        default=3.0,
        description="ボラティリティ急変時の取引停止閾値（ATRの倍数）",
    )


class NotifyConfig(BaseModel):
    """通知設定"""

    discord_webhook_url: str = Field(default="", description="Discord WebhookのURL")
    daily_report_hour: int = Field(
        default=9,
        description="日次レポートの送信時刻（時）",
    )


class Config(BaseSettings):
    """アプリケーション全体の設定

    .envファイルから環境変数を読み込み、config.tomlからその他の設定値を読み込む。
    環境変数はconfig.tomlの値を上書きする。
    """

    model_config = SettingsConfigDict(
        env_prefix="STELLA_",
        env_nested_delimiter="__",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    mode: Literal["live", "paper", "backtest"] = Field(
        default="paper",
        description="実行モード: live(本番), paper(ペーパー), backtest(バックテスト)",
    )
    exchange: ExchangeConfig = Field(default_factory=ExchangeConfig)
    strategies: list[StrategyConfig] = Field(
        default_factory=list,
        description="有効な戦略のリスト",
    )
    safety: SafetyConfig = Field(default_factory=SafetyConfig)
    notify: NotifyConfig = Field(default_factory=NotifyConfig)
    check_interval_sec: int = Field(
        default=60,
        description="メインループの実行間隔（秒）",
    )

    @classmethod
    def load(cls, config_path: Path | str | None = None) -> Config:
        """設定ファイルとenvから設定を読み込む

        Args:
            config_path: config.tomlのパス。Noneの場合はデフォルトパスを使用する。

        Returns:
            読み込まれた設定オブジェクト
        """
        toml_data: dict[str, Any] = {}

        if config_path is None:
            config_path = Path("config.toml")

        config_path = Path(config_path)

        if config_path.exists():
            logger.info("設定ファイルを読み込み中", path=str(config_path))
            with open(config_path, "rb") as f:
                toml_data = tomllib.load(f)
        else:
            logger.warning("設定ファイルが見つかりません。デフォルト値を使用します", path=str(config_path))

        # tomlの値で初期化し、環境変数で上書きする
        config = cls(**toml_data)
        logger.info(
            "設定を読み込みました",
            mode=config.mode,
            strategies_count=len(config.strategies),
        )
        return config
