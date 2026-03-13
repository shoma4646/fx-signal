"""
bitbank取引所アダプター

ccxt経由でbitbank APIに接続し、BaseExchangeインターフェースを実装する。
ペーパートレードモードに対応。

bitbank固有の仕様:
- 取引ペアはJPY建て（BTC/JPY, ETH/JPY等）
- APIレート制限: 読み取り10回/秒、書き込み6回/秒
- 同時発注件数: ペアあたり30件まで
- Maker手数料: -0.02%（報酬）、Taker手数料: 0.12%
"""

from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any

import ccxt.async_support as ccxt
import pandas as pd
import structlog

from stella.exchange.base import BaseExchange

logger = structlog.get_logger()

# リトライ設定
MAX_RETRIES = 3
RETRY_DELAY_SEC = 1.0

# レートリミット: bitbankは書き込み6回/秒、読み取り10回/秒
# 安全マージンをとって0.2秒間隔
RATE_LIMIT_SEC = 0.2


class BitbankExchange(BaseExchange):
    """bitbank取引所のccxtアダプター

    ccxt.bitbankを使用してbitbank APIと通信する。
    リトライ機構、レートリミット、ペーパートレード対応を含む。

    bitbankはテストネットを提供していないため、
    ペーパートレードモードでの検証を推奨する。
    """

    def __init__(
        self,
        api_key: str = "",
        api_secret: str = "",
        paper: bool = False,
    ) -> None:
        """BitbankExchangeを初期化する

        Args:
            api_key: bitbank APIキー
            api_secret: bitbank APIシークレット
            paper: ペーパートレードモード（注文を実際には送信しない）
        """
        self._paper = paper
        self._last_request_time: float = 0.0
        # ペーパートレード用の仮想注文・残高
        self._paper_orders: list[dict[str, Any]] = []
        self._paper_balance: dict[str, Any] = {
            "JPY": {"free": 1000000.0, "used": 0.0, "total": 1000000.0},
        }

        self._exchange = ccxt.bitbank(
            {
                "apiKey": api_key,
                "secret": api_secret,
            }
        )

        logger.info(
            "bitbank取引所アダプターを初期化しました",
            paper=paper,
        )

    async def close(self) -> None:
        """ccxtセッションを閉じる"""
        await self._exchange.close()

    async def _rate_limit(self) -> None:
        """レートリミットを適用する

        前回のリクエストから最小間隔が経過していない場合、待機する。
        """
        elapsed = time.monotonic() - self._last_request_time
        if elapsed < RATE_LIMIT_SEC:
            await asyncio.sleep(RATE_LIMIT_SEC - elapsed)
        self._last_request_time = time.monotonic()

    async def _retry(self, coro_factory: Any, operation: str) -> Any:
        """リトライ機構付きでAPI呼び出しを実行する

        Args:
            coro_factory: リトライ可能なコルーチンを生成する呼び出し可能オブジェクト
            operation: ログ用の操作名

        Returns:
            API呼び出しの結果

        Raises:
            ccxt.BaseError: 最大リトライ回数を超えた場合
        """
        last_error: Exception | None = None

        for attempt in range(1, MAX_RETRIES + 1):
            try:
                await self._rate_limit()
                result = await coro_factory()
                return result
            except (ccxt.NetworkError, ccxt.ExchangeNotAvailable) as e:
                last_error = e
                logger.warning(
                    "API呼び出しに失敗。リトライします",
                    operation=operation,
                    attempt=attempt,
                    max_retries=MAX_RETRIES,
                    error=str(e),
                )
                if attempt < MAX_RETRIES:
                    await asyncio.sleep(RETRY_DELAY_SEC * attempt)
            except ccxt.BaseError:
                # 認証エラーやパラメータエラーなどはリトライしない
                raise

        logger.error(
            "API呼び出しが最大リトライ回数に達しました",
            operation=operation,
            error=str(last_error),
        )
        raise last_error  # type: ignore[misc]

    async def get_ticker(self, symbol: str) -> dict[str, Any]:
        """指定シンボルの最新ティッカー情報を取得する

        Args:
            symbol: 通貨ペア（例: "BTC/JPY"）

        Returns:
            ティッカー情報を含む辞書
        """
        result = await self._retry(
            lambda: self._exchange.fetch_ticker(symbol),
            f"get_ticker({symbol})",
        )
        return result

    async def get_balance(self) -> dict[str, Any]:
        """口座残高を取得する

        ペーパートレードモードの場合は仮想残高を返す。

        Returns:
            通貨ごとの残高情報を含む辞書
        """
        if self._paper:
            return self._paper_balance

        result = await self._retry(
            lambda: self._exchange.fetch_balance(),
            "get_balance",
        )
        return result

    async def get_ohlcv(
        self,
        symbol: str,
        timeframe: str = "1h",
        limit: int = 100,
    ) -> pd.DataFrame:
        """OHLCV（ローソク足）データを取得する

        Args:
            symbol: 通貨ペア（例: "BTC/JPY"）
            timeframe: 時間足（例: "1m", "5m", "1h", "1d"）
            limit: 取得するローソク足の本数

        Returns:
            timestamp, open, high, low, close, volumeカラムを持つDataFrame
        """
        raw = await self._retry(
            lambda: self._exchange.fetch_ohlcv(symbol, timeframe, limit=limit),
            f"get_ohlcv({symbol}, {timeframe})",
        )

        df = pd.DataFrame(
            raw,
            columns=["timestamp", "open", "high", "low", "close", "volume"],
        )
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
        df.set_index("timestamp", inplace=True)
        return df

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

        ペーパートレードモードの場合は仮想注文を生成して返す。

        bitbank固有の注意:
        - 成行注文（market）と指値注文（limit）に対応
        - 指値注文はMaker手数料-0.02%（報酬）のため推奨
        - 同時発注件数は1ペアあたり30件まで

        Args:
            symbol: 通貨ペア（例: "BTC/JPY"）
            order_type: 注文タイプ（"market" または "limit"）
            side: 売買方向（"buy" または "sell"）
            amount: 注文数量
            price: 指値価格（成行の場合はNone）
            params: 取引所固有の追加パラメータ

        Returns:
            注文情報を含む辞書
        """
        if self._paper:
            return self._create_paper_order(symbol, order_type, side, amount, price)

        result = await self._retry(
            lambda: self._exchange.create_order(
                symbol, order_type, side, amount, price, params or {}
            ),
            f"create_order({symbol}, {side}, {amount})",
        )
        logger.info(
            "注文を作成しました",
            symbol=symbol,
            side=side,
            order_type=order_type,
            amount=amount,
            order_id=result.get("id"),
        )
        return result

    def _create_paper_order(
        self,
        symbol: str,
        order_type: str,
        side: str,
        amount: float,
        price: float | None,
    ) -> dict[str, Any]:
        """ペーパートレード用の仮想注文を作成する

        Args:
            symbol: 通貨ペア
            order_type: 注文タイプ
            side: 売買方向
            amount: 注文数量
            price: 指値価格

        Returns:
            仮想注文情報を含む辞書
        """
        order_id = str(uuid.uuid4())
        # 成行注文はすぐに約定したとみなす
        status = "closed" if order_type == "market" else "open"
        order = {
            "id": order_id,
            "symbol": symbol,
            "type": order_type,
            "side": side,
            "amount": amount,
            "price": price,
            "status": status,
            "filled": amount if status == "closed" else 0.0,
            "remaining": 0.0 if status == "closed" else amount,
            "timestamp": int(time.time() * 1000),
            "paper": True,
        }
        self._paper_orders.append(order)
        logger.info(
            "ペーパートレード注文を作成しました",
            order_id=order_id,
            symbol=symbol,
            side=side,
            amount=amount,
        )
        return order

    async def cancel_order(self, order_id: str, symbol: str) -> dict[str, Any]:
        """注文をキャンセルする

        Args:
            order_id: キャンセルする注文のID
            symbol: 通貨ペア（例: "BTC/JPY"）

        Returns:
            キャンセル結果を含む辞書
        """
        if self._paper:
            for order in self._paper_orders:
                if order["id"] == order_id:
                    order["status"] = "canceled"
                    order["remaining"] = 0.0
                    logger.info("ペーパートレード注文をキャンセルしました", order_id=order_id)
                    return order
            raise ValueError(f"注文が見つかりません: {order_id}")

        result = await self._retry(
            lambda: self._exchange.cancel_order(order_id, symbol),
            f"cancel_order({order_id}, {symbol})",
        )
        logger.info("注文をキャンセルしました", order_id=order_id, symbol=symbol)
        return result

    async def get_open_orders(self, symbol: str | None = None) -> list[dict[str, Any]]:
        """未約定の注文一覧を取得する

        Args:
            symbol: 通貨ペア。Noneの場合は全ペアの未約定注文を返す。

        Returns:
            未約定注文のリスト
        """
        if self._paper:
            orders = [o for o in self._paper_orders if o["status"] == "open"]
            if symbol:
                orders = [o for o in orders if o["symbol"] == symbol]
            return orders

        result = await self._retry(
            lambda: self._exchange.fetch_open_orders(symbol),
            f"get_open_orders({symbol})",
        )
        return result

    async def fetch_my_trades(
        self,
        symbol: str,
        since: int | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        """自分の約定履歴を取得する

        Args:
            symbol: 通貨ペア（例: "BTC/JPY"）
            since: 取得開始タイムスタンプ（ミリ秒）
            limit: 取得件数の上限

        Returns:
            約定履歴のリスト
        """
        if self._paper:
            trades = [
                o for o in self._paper_orders if o["symbol"] == symbol and o["status"] == "closed"
            ]
            if since:
                trades = [t for t in trades if t["timestamp"] >= since]
            if limit:
                trades = trades[-limit:]
            return trades

        result = await self._retry(
            lambda: self._exchange.fetch_my_trades(symbol, since, limit),
            f"fetch_my_trades({symbol})",
        )
        return result
