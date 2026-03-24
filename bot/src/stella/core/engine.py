"""トレーディングエンジンモジュール。

メインのトレーディングループを管理し、戦略の実行、シグナルの検証、
注文の実行、通知の送信を統合的に制御する。
"""

from __future__ import annotations

import asyncio
import traceback
from pathlib import Path
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
            exchange_config = getattr(self._config, "exchange", None)
            if isinstance(exchange_config, dict):
                api_key = exchange_config.get("api_key", "")
                api_secret = exchange_config.get("api_secret", "")
                exchange_name = exchange_config.get("name", "bitbank")
            else:
                api_key = getattr(exchange_config, "api_key", "")
                api_secret = getattr(exchange_config, "api_secret", "")
                exchange_name = getattr(exchange_config, "name", "bitbank")

            is_paper = getattr(self._config, "mode", "paper") == "paper"

            if exchange_name == "bitbank":
                from stella.exchange.bitbank import BitbankExchange

                self._exchange = BitbankExchange(
                    api_key=api_key,
                    api_secret=api_secret,
                    paper=is_paper,
                )
            else:
                from stella.exchange.bybit import BybitExchange

                testnet = (
                    exchange_config.get("testnet", True)
                    if isinstance(exchange_config, dict)
                    else getattr(exchange_config, "testnet", True)
                )
                self._exchange = BybitExchange(
                    api_key=api_key,
                    api_secret=api_secret,
                    testnet=testnet,
                    paper=is_paper,
                )

            if hasattr(self._exchange, "initialize"):
                await self._exchange.initialize()
            logger.info("取引所クライアントを初期化しました", exchange=exchange_name, paper=is_paper)
        except ImportError:
            logger.warning("取引所モジュールが見つかりません。モックモードで動作します。")
            self._exchange = None

        # ポートフォリオマネージャーの初期化
        self._portfolio = PortfolioManager(
            initial_balance=10000.0,
            max_positions=3,
        )

        # 安全機構の初期化
        from stella.core.safety import SafetyConfig as SafetyDataConfig

        safety_cfg = getattr(self._config, "safety", None)
        if safety_cfg is not None and not isinstance(safety_cfg, dict):
            safety_data_config = SafetyDataConfig(
                daily_loss_limit=getattr(safety_cfg, "daily_loss_limit_pct", 5.0) / 100,
                max_drawdown=getattr(safety_cfg, "max_drawdown_pct", 20.0) / 100,
                max_trade_risk=getattr(safety_cfg, "risk_per_trade_pct", 2.0) / 100,
                volatility_multiplier=getattr(safety_cfg, "volatility_pause_atr_multiplier", 3.0),
            )
        else:
            safety_data_config = SafetyDataConfig()

        self._safety = SafetyManager(safety_data_config, self._portfolio)

        # 戦略の初期化
        strategy_configs = getattr(self._config, "strategies", [])
        if not strategy_configs:
            # デフォルトでトレンドフォロー戦略を追加
            self._strategies.append(TrendStrategy())
            self._trading_pairs = ["BTC/USDT"]
            logger.info("デフォルトのトレンドフォロー戦略を追加しました")
        else:
            self._trading_pairs = []
            for sc in strategy_configs:
                if isinstance(sc, dict):
                    strategy_name = sc.get("name", "trend_follow")
                    pairs = sc.get("pairs", ["BTC/USDT"])
                    params = sc.get("params", {})
                else:
                    strategy_name = getattr(sc, "name", "trend_follow")
                    pairs = getattr(sc, "pairs", ["BTC/USDT"])
                    params = getattr(sc, "params", {})

                self._trading_pairs.extend(p for p in pairs if p not in self._trading_pairs)

                if "trend" in strategy_name:
                    self._strategies.append(TrendStrategy(params))
                else:
                    logger.warning("未知の戦略タイプです", strategy_name=strategy_name)

        # Discord通知の初期化
        notify_config = getattr(self._config, "notify", None)
        discord_webhook = (
            getattr(notify_config, "discord_webhook_url", None)
            if notify_config is not None
            else None
        ) or getattr(self._config, "discord_webhook_url", None)
        if discord_webhook:
            self._notifier = DiscordNotifier(discord_webhook)
            logger.info("Discord通知を有効化しました")
        else:
            logger.info("Discord通知は無効です (webhook_urlが未設定)")

        # ポートフォリオ・安全機構の状態を復元
        self._state_dir = Path(getattr(self._config, "state_dir", ".state"))
        self._load_state()

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

        # 3. 日次リセットチェック
        self._check_daily_reset()

        # 4. 安全チェック（ボラティリティ含む）
        if not self._check_safety(ohlcv_data):
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
                        await self.execute_signal(signal, strategy, ohlcv_df=df)
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

    async def execute_signal(
        self, signal: Signal, strategy: BaseStrategy, ohlcv_df: Any = None
    ) -> None:
        """単一のシグナルを実行する。

        ポートフォリオマネージャーで検証後、取引所に注文を送信する。

        Args:
            signal: 実行するトレーディングシグナル
            strategy: シグナルを生成した戦略
            ohlcv_df: OHLCVデータ（ATR計算用）
        """
        logger.info(
            "シグナルを受信しました",
            action=signal.action,
            symbol=signal.symbol,
            strength=signal.strength,
            reason=signal.reason,
        )

        # ATRをOHLCVデータから直接計算
        atr = self._calculate_atr_from_ohlcv(ohlcv_df) if ohlcv_df is not None else 0.0
        if atr <= 0:
            logger.warning("ATR値が計算できません。デフォルト値を使用します。", symbol=signal.symbol)
            atr = 1.0

        balance = self._portfolio._balance

        # 買いシグナルの場合: ポジションサイズ計算と注文前バリデーション
        if signal.action == "buy":
            quantity = strategy.get_position_size(signal, balance, atr)
            if quantity <= 0:
                logger.info("ポジションサイズが0のためスキップします", symbol=signal.symbol)
                return

            # 概算価格でバリデーション
            price_estimate = ohlcv_df["close"].iloc[-1] if ohlcv_df is not None else 0.0
            is_valid, reason = self._portfolio.validate_order(
                symbol=signal.symbol,
                side="buy",
                quantity=quantity,
                price=price_estimate,
            )
            if not is_valid:
                logger.info("注文バリデーションで却下されました", reason=reason)
                return

        elif signal.action == "sell":
            # 売りの場合: 既存ポジションの数量を使用
            position = self._portfolio.get_position(signal.symbol)
            if position is None:
                logger.info("売却対象のポジションがありません", symbol=signal.symbol)
                return
            quantity = position.quantity
        else:
            return

        # 注文の実行
        try:
            if self._exchange is not None:
                order = await self._exchange.create_order(
                    symbol=signal.symbol,
                    order_type="market",
                    side=signal.action,
                    amount=quantity,
                )

                price = float(order.get("average", order.get("price", 0))) if isinstance(order, dict) else 0.0

                logger.info(
                    "注文を実行しました",
                    action=signal.action,
                    symbol=signal.symbol,
                    quantity=quantity,
                    price=price,
                )

                # ポートフォリオを更新
                if signal.action == "buy":
                    self._portfolio.open_position(
                        symbol=signal.symbol,
                        side="buy",
                        entry_price=price,
                        quantity=quantity,
                        strategy_name=strategy.name,
                        stop_loss_price=signal.stop_loss,
                    )
                elif signal.action == "sell":
                    position = self._portfolio.get_position(signal.symbol)
                    if position is not None:
                        closed = self._portfolio.close_position(position.position_id, price)
                        # 安全機構にトレード結果を記録
                        if self._safety:
                            self._safety.record_trade(closed.realized_pnl)

                # 戦略にトレード記録
                strategy.record_trade()

                # 通知
                if self._notifier:
                    pnl = None
                    if signal.action == "sell" and isinstance(order, dict):
                        pnl = order.get("pnl")
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

    @staticmethod
    def _calculate_atr_from_ohlcv(df: Any, period: int = 14) -> float:
        """OHLCVデータからATRを計算する。

        Args:
            df: OHLCVデータを含むDataFrame
            period: ATR期間

        Returns:
            ATR値。計算不能な場合は0.0。
        """
        import pandas as pd

        if df is None or len(df) < 2:
            return 0.0

        high = df["high"]
        low = df["low"]
        close = df["close"]

        tr1 = high - low
        tr2 = (high - close.shift(1)).abs()
        tr3 = (low - close.shift(1)).abs()
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

        atr = tr.ewm(span=min(period, len(df)), adjust=False).mean()

        last_atr = atr.iloc[-1]
        return float(last_atr) if not pd.isna(last_atr) else 0.0

    async def shutdown(self) -> None:
        """エンジンを安全に停止する。

        実行中のループを停止し、状態を永続化してリソースを解放する。
        """
        logger.info("エンジンのシャットダウンを開始します")
        self._running = False

        # 状態を永続化
        self._save_state()

        if self._notifier:
            await self._notifier.send("トレーディングエンジンを停止します", level="warning")
            await self._notifier.close()

        if self._exchange and hasattr(self._exchange, "close"):
            await self._exchange.close()

        logger.info("エンジンのシャットダウンが完了しました")

    def _save_state(self) -> None:
        """ポートフォリオと安全機構の状態を永続化する。"""
        if not hasattr(self, "_state_dir"):
            return
        try:
            if self._portfolio:
                self._portfolio.save_state(self._state_dir / "portfolio.json")
            if self._safety:
                self._safety.save_state(self._state_dir / "safety.json")
            logger.info("状態を保存しました", path=str(self._state_dir))
        except Exception as e:
            logger.error("状態の保存に失敗しました", error=str(e))

    def _load_state(self) -> None:
        """ポートフォリオと安全機構の状態を復元する。"""
        try:
            portfolio_path = self._state_dir / "portfolio.json"
            if portfolio_path.exists() and self._portfolio:
                self._portfolio.load_state(portfolio_path)
                logger.info("ポートフォリオ状態を復元しました")

            safety_path = self._state_dir / "safety.json"
            if safety_path.exists() and self._safety:
                self._safety.load_state(safety_path)
                logger.info("安全機構の状態を復元しました")
        except Exception as e:
            logger.warning("状態の復元に失敗しました（初回起動の場合は正常）", error=str(e))

    async def _fetch_ohlcv_data(self) -> dict[str, Any]:
        """全ペアのOHLCVデータを取得する。

        Returns:
            シンボルをキーとするDataFrameの辞書
        """
        import pandas as pd

        trading_pairs = getattr(self, "_trading_pairs", ["BTC/USDT"])
        timeframe = "1h"
        ohlcv_limit = 100
        result: dict[str, Any] = {}

        for symbol in trading_pairs:
            try:
                if self._exchange and hasattr(self._exchange, "get_ohlcv"):
                    df = await self._exchange.get_ohlcv(
                        symbol, timeframe=timeframe, limit=ohlcv_limit
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
                if hasattr(self._exchange, "get_balance"):
                    balance = await self._exchange.get_balance()
                    if hasattr(self._portfolio, "sync_with_exchange"):
                        await self._portfolio.sync_with_exchange(self._exchange)
                    elif hasattr(self._portfolio, "sync_balance"):
                        self._portfolio.sync_balance(balance)

                logger.debug("ポートフォリオを同期しました")
            except Exception as e:
                logger.error("ポートフォリオ同期に失敗しました", error=str(e))

    def _check_safety(self, ohlcv_data: dict[str, Any] | None = None) -> bool:
        """安全チェックを実行する。

        Args:
            ohlcv_data: OHLCVデータ（ボラティリティチェック用）

        Returns:
            安全ならTrue、取引停止が必要ならFalse
        """
        if not self._safety:
            return True

        # ボラティリティチェック（OHLCVデータがある場合）
        if ohlcv_data:
            for symbol, df in ohlcv_data.items():
                atr = self._calculate_atr_from_ohlcv(df)
                if atr > 0 and len(df) > 30:
                    # 長期平均ATRを計算
                    import pandas as pd

                    high = df["high"]
                    low = df["low"]
                    close = df["close"]
                    tr1 = high - low
                    tr2 = (high - close.shift(1)).abs()
                    tr3 = (low - close.shift(1)).abs()
                    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
                    avg_atr = float(tr.mean())

                    if avg_atr > 0:
                        self._safety.check_volatility(atr, avg_atr)

        can_trade, reason = self._safety.can_trade()
        if not can_trade:
            logger.warning("安全チェックにより取引停止", reason=reason)
            return False

        return True

    def _check_daily_reset(self) -> None:
        """日付が変わった場合に日次カウンターをリセットする。"""
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc)
        last_reset = getattr(self, "_last_daily_reset", None)

        if last_reset is None or last_reset.date() < now.date():
            if self._portfolio:
                self._portfolio.reset_daily()
            if self._safety:
                self._safety.reset_daily()
            self._last_daily_reset = now
            logger.info("日次カウンターをリセットしました")

    async def _update_trailing_stops(self, ohlcv_data: dict[str, Any]) -> None:
        """既存ポジションのトレーリングストップを更新する。

        Args:
            ohlcv_data: シンボルをキーとするDataFrameの辞書
        """
        if not self._portfolio:
            return

        positions = self._portfolio.get_open_positions()
        if not positions:
            return

        for position in positions:
            symbol = position.symbol
            if symbol in ohlcv_data:
                df = ohlcv_data[symbol]
                current_price = float(df["close"].iloc[-1])

                # 未実現損益を更新
                if position.side == "buy":
                    position.unrealized_pnl = (current_price - position.entry_price) * position.quantity
                else:
                    position.unrealized_pnl = (position.entry_price - current_price) * position.quantity

                logger.debug(
                    "ポジション情報を更新しました",
                    symbol=symbol,
                    current_price=current_price,
                    unrealized_pnl=round(position.unrealized_pnl, 2),
                )
