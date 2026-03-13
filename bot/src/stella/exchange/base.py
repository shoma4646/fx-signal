"""
取引所抽象基底クラス

すべての取引所実装が準拠すべきインターフェースを定義する。
ccxtのデータ構造に準拠した型定義を含む。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

import pandas as pd


class BaseExchange(ABC):
    """取引所の抽象基底クラス

    すべての取引所アダプターはこのクラスを継承し、
    各abstractメソッドを実装する必要がある。
    """

    @abstractmethod
    async def get_ticker(self, symbol: str) -> dict[str, Any]:
        """指定シンボルの最新ティッカー情報を取得する

        Args:
            symbol: 通貨ペア（例: "BTC/USDT"）

        Returns:
            ティッカー情報を含む辞書。last, bid, ask, volumeなどを含む。
        """
        ...

    @abstractmethod
    async def get_balance(self) -> dict[str, Any]:
        """口座残高を取得する

        Returns:
            通貨ごとの残高情報を含む辞書。free, used, totalを含む。
        """
        ...

    @abstractmethod
    async def get_ohlcv(
        self,
        symbol: str,
        timeframe: str = "1h",
        limit: int = 100,
    ) -> pd.DataFrame:
        """OHLCV（ローソク足）データを取得する

        Args:
            symbol: 通貨ペア（例: "BTC/USDT"）
            timeframe: 時間足（例: "1m", "5m", "1h", "1d"）
            limit: 取得するローソク足の本数

        Returns:
            timestamp, open, high, low, close, volumeカラムを持つDataFrame
        """
        ...

    @abstractmethod
    async def create_order(
        self,
        symbol: str,
        order_type: str,
        side: str,
        amount: float,
        price: float | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """注文を作成する

        Args:
            symbol: 通貨ペア（例: "BTC/USDT"）
            order_type: 注文タイプ（"market" または "limit"）
            side: 売買方向（"buy" または "sell"）
            amount: 注文数量
            price: 指値価格（成行の場合はNone）
            params: 取引所固有の追加パラメータ

        Returns:
            注文情報を含む辞書。id, status, filledなどを含む。
        """
        ...

    @abstractmethod
    async def cancel_order(self, order_id: str, symbol: str) -> dict[str, Any]:
        """注文をキャンセルする

        Args:
            order_id: キャンセルする注文のID
            symbol: 通貨ペア（例: "BTC/USDT"）

        Returns:
            キャンセル結果を含む辞書
        """
        ...

    @abstractmethod
    async def get_open_orders(self, symbol: str | None = None) -> list[dict[str, Any]]:
        """未約定の注文一覧を取得する

        Args:
            symbol: 通貨ペア。Noneの場合は全ペアの未約定注文を返す。

        Returns:
            未約定注文のリスト
        """
        ...

    @abstractmethod
    async def fetch_my_trades(
        self,
        symbol: str,
        since: int | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        """自分の約定履歴を取得する

        Args:
            symbol: 通貨ペア（例: "BTC/USDT"）
            since: 取得開始タイムスタンプ（ミリ秒）。Noneの場合は最新から取得。
            limit: 取得件数の上限

        Returns:
            約定履歴のリスト
        """
        ...
