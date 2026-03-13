"""戦略基底クラスモジュール。

すべてのトレーディング戦略が継承する抽象基底クラスと、
シグナルデータクラスを定義する。
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

import pandas as pd
import structlog

if TYPE_CHECKING:
    from stella.core.portfolio import PortfolioManager

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class Signal:
    """トレーディングシグナルを表すデータクラス。

    戦略の分析結果として生成され、ポートフォリオマネージャーに渡される。

    Attributes:
        action: 売買アクション ("buy", "sell", "hold")
        symbol: 対象シンボル (例: "BTC/USDT")
        strength: シグナル強度 (0.0 - 1.0)
        reason: シグナル発生理由の説明
        stop_loss: ストップロス価格 (Noneの場合はデフォルト値を使用)
        take_profit: テイクプロフィット価格 (Noneの場合は設定なし)
    """

    action: Literal["buy", "sell", "hold"]
    symbol: str
    strength: float
    reason: str
    stop_loss: float | None = None
    take_profit: float | None = None

    def __post_init__(self) -> None:
        """バリデーションを実行する。"""
        if not 0.0 <= self.strength <= 1.0:
            raise ValueError(f"strengthは0.0から1.0の範囲で指定してください: {self.strength}")
        if self.action not in ("buy", "sell", "hold"):
            raise ValueError(f"無効なaction: {self.action}")


class BaseStrategy(ABC):
    """トレーディング戦略の抽象基底クラス。

    すべての戦略はこのクラスを継承し、analyze()とget_position_size()を実装する。
    クールダウン管理やトレード記録などの共通機能を提供する。

    Attributes:
        _name: 戦略名
        _config: 戦略設定パラメータ
        _is_active: 戦略が有効かどうか
        _last_trade_time: 最後にトレードを実行した時刻 (UNIX timestamp)
        _cooldown_seconds: クールダウン期間 (秒)
        _trade_history: トレード時刻の履歴
    """

    def __init__(self, name: str, config: dict) -> None:
        """戦略を初期化する。

        Args:
            name: 戦略の識別名
            config: 戦略固有の設定パラメータ
        """
        self._name = name
        self._config = config
        self._is_active = True
        self._last_trade_time: float = 0.0
        self._cooldown_seconds: float = config.get("cooldown_minutes", 60) * 60
        self._trade_history: list[float] = field(default_factory=list) if False else []
        logger.info("戦略を初期化しました", strategy=name, config=config)

    @property
    def name(self) -> str:
        """戦略名を返す。"""
        return self._name

    @property
    def is_active(self) -> bool:
        """戦略が有効かどうかを返す。"""
        return self._is_active

    @is_active.setter
    def is_active(self, value: bool) -> None:
        """戦略の有効/無効を設定する。"""
        self._is_active = value
        logger.info("戦略の状態を変更しました", strategy=self._name, is_active=value)

    def is_cooldown(self) -> bool:
        """クールダウン期間中かどうかを判定する。

        最後のトレードからcooldown_seconds秒が経過していない場合はTrueを返す。

        Returns:
            クールダウン中ならTrue
        """
        if self._last_trade_time == 0.0:
            return False
        elapsed = time.time() - self._last_trade_time
        in_cooldown = elapsed < self._cooldown_seconds
        if in_cooldown:
            logger.debug(
                "クールダウン中です",
                strategy=self._name,
                remaining_sec=round(self._cooldown_seconds - elapsed, 1),
            )
        return in_cooldown

    def record_trade(self) -> None:
        """トレード実行時刻を記録する。

        クールダウン管理に使用される。
        """
        now = time.time()
        self._last_trade_time = now
        self._trade_history.append(now)
        logger.info("トレードを記録しました", strategy=self._name, timestamp=now)

    @abstractmethod
    async def analyze(
        self, df: pd.DataFrame, portfolio: PortfolioManager
    ) -> list[Signal]:
        """市場データを分析してシグナルを生成する。

        Args:
            df: OHLCVデータを含むDataFrame
                (columns: open, high, low, close, volume)
            portfolio: ポートフォリオマネージャー

        Returns:
            生成されたシグナルのリスト
        """
        ...

    @abstractmethod
    def get_position_size(self, signal: Signal, balance: float, atr: float) -> float:
        """シグナルに基づくポジションサイズを計算する。

        リスク管理に基づいたポジションサイズを返す。

        Args:
            signal: トレーディングシグナル
            balance: 現在の利用可能残高
            atr: 現在のATR値

        Returns:
            ポジションサイズ (数量)
        """
        ...
