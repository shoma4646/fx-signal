"""トレーディングエンジンモジュール。

メインのトレーディングループを管理し、戦略の実行、シグナルの検証、
注文の実行、通知の送信を統合的に制御する。
"""

from __future__ import annotations

import asyncio
import traceback
from typing import TYPE_CHECKING, Any

import structlog

from stella.core.portfolio import PortfolioManager
from stella.core.safety import SafetyManager
from stella.notify.discord import DiscordNotifier
from stella.strategies.base import BaseStrategy, Signal
from stella.strategies.trend import TrendStrategy

if TYPE_CHECKING:
    from stella.config import Config

logger = structlog.get_logger(__name__)


class TradingEngine:
    """トレーディングエンジン。

    戦略の実行、ポートフォリオ管理、安全機構、通知を統合し、
    定期的なトレーディングループを実行する。

    Attributes:
        _config: アプリケーション設定
        _portfolio: ポートフォリオマネージャー
        _safety: 安全機構マネージャー
        _strategies: 登録された戦略のリスト
        _notifier: Discord通知
        _exchange: 取引所クライアント
        _running: エンジンが実行中かどうか
        _check_interval: チェック間隔 (秒)
    """

    def __init__(self, config: Config) -> None:
        """トレーディングエンジンを初期化する。

        Args:
            config: アプリケーション設定
        """
        self._config = config
        self._portfolio: PortfolioManager | None = None
        self._safety: SafetyManager | None = None
        self._strategies: list[BaseStrategy] = []
        self._notifier: DiscordNotifier | None = None
        self._exchange: Any = None
        self._running = False
        self._check_interval: float = getattr(config, "check_interval_sec", 60.0)
        logger.info("トレーディングエンジンを作成しました")

    async def initialize(self) -> None:
        """エンジンを初期化する。

        取引所クライアント、ポートフォリオマネージャー、安全機構、
        戦略、通知の各コンポーネントをセットアップする。
        """
        logger.info("エンジンの初期化を開始します")

        # 取引所クライアントの初期化
        try:
            from stella.exchange.bybit import BybitExchange

            exchange_config = getattr(self._config, "exchange", {})
            if isinstance(exchange_config, dict):
                self._exchange = BybitExchange(exchange_config)
            else:
                self._exchange = BybitExchange(
                    {
                        "api_key": getattr(exchange_config, "api_key", ""),
                        "api_secret": getattr(exchange_config, "api_secret", ""),
                        "testnet": getattr(exchange_config, "testnet", True),
                    }
                )
            if hasattr(self._exchange, "initialize"):
                await self._exchange.initialize()
            logger.info("取引所クライアントを初期化しました")
        except ImportError:
            logger.warning("取引所モジュールが見つかりません。モックモードで動作します。")
            self._exchange = None

        # ポートフォリオマネージャーの初期化
        portfolio_config = getattr(self._config, "portfolio", {})
        if isinstance(portfolio_config, dict):
            self._portfolio = PortfolioManager(portfolio_config)
        else:
            self._portfolio = PortfolioManager(
                {
                    "max_positions": getattr(portfolio_config, "max_positions", 3),
                    "max_position_pct": getattr(portfolio_config, "max_position_pct", 0.3),
                }
            )

        # 安全機構の初期化
        safety_config = getattr(self._config, "safety", {})
        if isinstance(safety_config, dict):
            self._safety = SafetyManager(safety_config)
        else:
            self._safety = SafetyManager(
                {
                    "daily_loss_limit_pct": getattr(safety_config, "daily_loss_limit_pct", 0.05),
                    "max_drawdown_pct": getattr(safety_config, "max_drawdown_pct", 0.20),
                }
            )

        # 戦略の初期化
        strategy_configs = getattr(self._config, "strategies", [])
        if not strategy_configs:
            # デフォルトでトレンドフォロー戦略を追加
            self._strategies.append(TrendStrategy())
            logger.info("デフォルトのトレンドフォロー戦略を追加しました")
        else:
            for strategy_config in strategy_configs:
                if isinstance(strategy_config, dict):
                    strategy_type = strategy_config.get("type", "trend")
                else:
                    strategy_type = getattr(strategy_config, "type", "trend")
                    strategy_config = dict(strategy_config) if hasattr(strategy_config, "__iter__") else {}

                if strategy_type == "trend":
                    self._strategies.append(TrendStrategy(strategy_config))
                else:
                    logger.warning("未知の戦略タイプです", strategy_type=strategy_type)

        # Discord通知の初期化
        discord_webhook = getattr(self._config, "discord_webhook_url", None)
        if discord_webhook:
            self._notifier = DiscordNotifier(discord_webhook)
            logger.info("Discord通知を有効化しました")
        else:
            logger.info("Discord通知は無効です (webhook_urlが未設定)")

        logger.info(
            "エンジンの初期化が完了しました",
            strategies=len(self._strategies),
            mode=getattr(self._config, "mode", "paper"),
        )

    async def run(self) -> None:
        """メインのトレーディングループを実行する。

        check_interval_sec間隔でrun_once()を繰り返し実行する。
        停止シグナルを受信するまで実行を継続する。
        """
        self._running = True
        logger.info(
            "トレーディングループを開始します",
            interval_sec=self._check_interval,
        )

        if self._notifier:
            await self._notifier.send("トレーディングエンジンを起動しました", level="info")

        while self._running:
            try:
                await self.run_once()
            except Exception as e:
                error_msg = f"{type(e).__name__}: {e}"
                logger.error("トレーディングループでエラーが発生しました", error=error_msg)
                if self._notifier:
                    await self._notifier.notify_error(error_msg)

            if self._running:
                await asyncio.sleep(self._check_interval)

    async def run_once(self) -> None:
        """1回のトレーディングサイクルを実行する。

        処理フロー:
        1. 全ペアのOHLCVデータを取得
        2. ポートフォリオを取引所と同期
        3. 安全チェック
        4. 各戦略のanalyze()を実行
        5. シグナルをポートフォリオマネージャーで検証
        6. 承認された注文を実行
        7. 既存ポジションのトレーリングストップを更新
        8. 通知を送信
        """
        if not self._portfolio or not self._safety:
            logger.error("エンジンが初期化されていません")
            return

        logger.debug("トレーディングサイクルを開始します")

        # 1. OHLCVデータの取得
        ohlcv_data = await self._fetch_ohlcv_data()
        if not ohlcv_data:
            logger.warning("OHLCVデータの取得に失敗しました")
            return

        # 2. ポートフォリオの同期
        await self._sync_portfolio()

        # 3. 安全チェック
        if not self._check_safety():
            return

        # 4-6. 各戦略でシグナルを生成・検証・実行
        for strategy in self._strategies:
            if not strategy.is_active:
                continue

            for symbol, df in ohlcv_data.items():
                try:
                    signals = await strategy.analyze(df, self._portfolio)
                    for signal in signals:
                        if signal.action == "hold":
                            continue
                        await self.execute_signal(signal, strategy)
                except Exception as e:
                    logger.error(
                        "戦略の実行中にエラーが発生しました",
                        strategy=strategy.name,
                        symbol=symbol,
                        error=str(e),
                        traceback=traceback.format_exc(),
                    )

        # 7. トレーリングストップの更新
        await self._update_trailing_stops(ohlcv_data)

        logger.debug("トレーディングサイクルが完了しました")

    async def execute_signal(self, signal: Signal, strategy: BaseStrategy) -> None:
        """単一のシグナルを実行する。

        ポートフォリオマネージャーで検証後、取引所に注文を送信する。

        Args:
            signal: 実行するトレーディングシグナル
            strategy: シグナルを生成した戦略
        """
        logger.info(
            "シグナルを受信しました",
            action=signal.action,
            symbol=signal.symbol,
            strength=signal.strength,
            reason=signal.reason,
        )

        # ポートフォリオマネージャーによる検証
        if hasattr(self._portfolio, "validate_signal"):
            is_valid = self._portfolio.validate_signal(signal)
            if not is_valid:
                logger.info(
                    "シグナルがポートフォリオバリデーションで却下されました",
                    symbol=signal.symbol,
                    action=signal.action,
                )
                return

        # ポジションサイズの計算
        balance = getattr(self._portfolio, "available_balance", 0.0)
        if callable(balance):
            balance = balance()

        # ATRの取得 (簡易的にデフォルト値を使用)
        atr = getattr(self._portfolio, "get_current_atr", lambda s: 0.0)(signal.symbol)
        if atr <= 0:
            logger.warning("ATR値が取得できません。デフォルト値を使用します。", symbol=signal.symbol)
            atr = 1.0

        quantity = strategy.get_position_size(signal, balance, atr)
        if quantity <= 0:
            logger.info("ポジションサイズが0のためスキップします", symbol=signal.symbol)
            return

        # 注文の実行
        try:
            if self._exchange is not None:
                if signal.action == "buy":
                    order = await self._exchange.create_market_buy(
                        signal.symbol, quantity
                    )
                elif signal.action == "sell":
                    order = await self._exchange.create_market_sell(
                        signal.symbol, quantity
                    )
                else:
                    return

                logger.info(
                    "注文を実行しました",
                    action=signal.action,
                    symbol=signal.symbol,
                    quantity=quantity,
                    order=order,
                )

                # ポートフォリオを更新
                if hasattr(self._portfolio, "record_trade"):
                    price = order.get("price", 0) if isinstance(order, dict) else 0
                    self._portfolio.record_trade(
                        symbol=signal.symbol,
                        action=signal.action,
                        price=price,
                        quantity=quantity,
                        stop_loss=signal.stop_loss,
                    )

                # 戦略にトレード記録
                strategy.record_trade()

                # 通知
                if self._notifier:
                    price = order.get("price", 0) if isinstance(order, dict) else 0
                    pnl = order.get("pnl") if isinstance(order, dict) else None
                    await self._notifier.notify_trade(
                        action=signal.action,
                        symbol=signal.symbol,
                        price=price,
                        quantity=quantity,
                        pnl=pnl,
                    )
            else:
                # 取引所未接続 (ペーパーモード)
                logger.info(
                    "ペーパーモード: 注文をシミュレートしました",
                    action=signal.action,
                    symbol=signal.symbol,
                    quantity=quantity,
                )

        except Exception as e:
            error_msg = f"注文実行エラー ({signal.symbol}): {e}"
            logger.error(error_msg, traceback=traceback.format_exc())
            if self._notifier:
                await self._notifier.notify_error(error_msg)

    async def shutdown(self) -> None:
        """エンジンを安全に停止する。

        実行中のループを停止し、リソースを解放する。
        """
        logger.info("エンジンのシャットダウンを開始します")
        self._running = False

        if self._notifier:
            await self._notifier.send("トレーディングエンジンを停止します", level="warning")
            await self._notifier.close()

        if self._exchange and hasattr(self._exchange, "close"):
            await self._exchange.close()

        logger.info("エンジンのシャットダウンが完了しました")

    async def _fetch_ohlcv_data(self) -> dict[str, Any]:
        """全ペアのOHLCVデータを取得する。

        Returns:
            シンボルをキーとするDataFrameの辞書
        """
        import pandas as pd

        trading_pairs = getattr(self._config, "trading_pairs", ["BTC/USDT", "ETH/USDT"])
        timeframe = getattr(self._config, "timeframe", "1h")
        ohlcv_limit = getattr(self._config, "ohlcv_limit", 100)
        result: dict[str, Any] = {}

        for symbol in trading_pairs:
            try:
                if self._exchange and hasattr(self._exchange, "fetch_ohlcv"):
                    raw = await self._exchange.fetch_ohlcv(
                        symbol, timeframe=timeframe, limit=ohlcv_limit
                    )
                    df = pd.DataFrame(
                        raw, columns=["timestamp", "open", "high", "low", "close", "volume"]
                    )
                    df["symbol"] = symbol
                    result[symbol] = df
                else:
                    logger.debug("取引所未接続のためOHLCVデータをスキップします", symbol=symbol)
            except Exception as e:
                logger.error(
                    "OHLCVデータの取得に失敗しました",
                    symbol=symbol,
                    error=str(e),
                )

        return result

    async def _sync_portfolio(self) -> None:
        """ポートフォリオを取引所と同期する。"""
        if self._exchange and self._portfolio:
            try:
                if hasattr(self._exchange, "fetch_balance"):
                    balance = await self._exchange.fetch_balance()
                    if hasattr(self._portfolio, "sync_balance"):
                        self._portfolio.sync_balance(balance)

                if hasattr(self._exchange, "fetch_positions"):
                    positions = await self._exchange.fetch_positions()
                    if hasattr(self._portfolio, "sync_positions"):
                        self._portfolio.sync_positions(positions)

                logger.debug("ポートフォリオを同期しました")
            except Exception as e:
                logger.error("ポートフォリオ同期に失敗しました", error=str(e))

    def _check_safety(self) -> bool:
        """安全チェックを実行する。

        Returns:
            安全ならTrue、取引停止が必要ならFalse
        """
        if not self._safety:
            return True

        if hasattr(self._safety, "is_kill_switch_active"):
            if self._safety.is_kill_switch_active():
                logger.warning("キルスイッチが有効です。取引を停止します。")
                return False

        if hasattr(self._safety, "check_safety"):
            is_safe = self._safety.check_safety(self._portfolio)
            if not is_safe:
                logger.warning("安全チェックに失敗しました。取引を停止します。")
                return False

        return True

    async def _update_trailing_stops(self, ohlcv_data: dict[str, Any]) -> None:
        """既存ポジションのトレーリングストップを更新する。

        Args:
            ohlcv_data: シンボルをキーとするDataFrameの辞書
        """
        if not self._portfolio or not hasattr(self._portfolio, "get_open_positions"):
            return

        positions = self._portfolio.get_open_positions()
        if not positions:
            return

        for position in positions:
            symbol = getattr(position, "symbol", None)
            if symbol and symbol in ohlcv_data:
                df = ohlcv_data[symbol]
                current_price = df["close"].iloc[-1]
                if hasattr(position, "update_highest_price"):
                    position.update_highest_price(current_price)
                    logger.debug(
                        "トレーリングストップを更新しました",
                        symbol=symbol,
                        current_price=current_price,
                    )
