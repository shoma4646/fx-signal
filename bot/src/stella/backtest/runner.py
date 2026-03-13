"""バックテスト実行モジュール。

過去のOHLCVデータに対して戦略を適用し、
パフォーマンス指標を算出するバックテストエンジンを提供する。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import numpy as np
import pandas as pd
import structlog

from stella.core.portfolio import PortfolioManager
from stella.strategies.base import BaseStrategy

logger = structlog.get_logger(__name__)


@dataclass
class Trade:
    """個別トレードの記録。

    Attributes:
        entry_time: エントリー時刻
        exit_time: 決済時刻
        symbol: 取引シンボル
        side: 売買方向 ("buy" / "sell")
        entry_price: エントリー価格
        exit_price: 決済価格
        quantity: 取引数量
        pnl: 損益(手数料込み)
        commission: 手数料合計
        reason_entry: エントリー理由
        reason_exit: 決済理由
    """

    entry_time: str
    exit_time: str
    symbol: str
    side: str
    entry_price: float
    exit_price: float
    quantity: float
    pnl: float
    commission: float
    reason_entry: str
    reason_exit: str


@dataclass
class BacktestResult:
    """バックテスト結果を格納するデータクラス。

    Attributes:
        total_return: 合計リターン(%)
        sharpe_ratio: シャープレシオ(年率換算)
        max_drawdown: 最大ドローダウン(%)
        win_rate: 勝率(%)
        profit_factor: プロフィットファクター
        total_trades: 総トレード数
        trades: 個別トレード記録のリスト
        equity_curve: 資産推移のリスト(各要素は{"timestamp": str, "equity": float})
        start_date: バックテスト開始日時
        end_date: バックテスト終了日時
    """

    total_return: float
    sharpe_ratio: float
    max_drawdown: float
    win_rate: float
    profit_factor: float
    total_trades: int
    trades: list[Trade]
    equity_curve: list[dict[str, Any]]
    start_date: str
    end_date: str


class BacktestRunner:
    """バックテスト実行エンジン。

    過去のOHLCVデータに対して戦略を適用し、仮想的にトレードを実行する。
    ポジション管理、手数料計算、パフォーマンス指標算出を行う。

    Attributes:
        _strategy: バックテスト対象の戦略
        _initial_balance: 初期残高
        _commission_rate: 手数料率(片道)
    """

    def __init__(
        self,
        strategy: BaseStrategy,
        initial_balance: float,
        commission_rate: float = 0.001,
    ) -> None:
        """BacktestRunnerを初期化する。

        Args:
            strategy: バックテスト対象のトレーディング戦略
            initial_balance: 初期残高(USDT)
            commission_rate: 手数料率(片道、デフォルト: 0.1%)
        """
        self._strategy = strategy
        self._initial_balance = initial_balance
        self._commission_rate = commission_rate
        logger.info(
            "バックテストランナーを初期化しました",
            strategy=strategy.name,
            initial_balance=initial_balance,
            commission_rate=commission_rate,
        )

    async def fetch_historical_data(
        self,
        exchange: Any,
        symbol: str,
        timeframe: str,
        since: int,
        limit: int,
    ) -> pd.DataFrame:
        """ccxt経由でOHLCVヒストリカルデータを取得する。

        Args:
            exchange: ccxt互換の取引所インスタンス
            symbol: 通貨ペア(例: "BTC/USDT")
            timeframe: 時間足(例: "1h", "4h", "1d")
            since: 取得開始タイムスタンプ(ミリ秒)
            limit: 取得するローソク足の本数

        Returns:
            timestamp, open, high, low, close, volumeカラムを持つDataFrame
        """
        logger.info(
            "ヒストリカルデータを取得します",
            symbol=symbol,
            timeframe=timeframe,
            since=since,
            limit=limit,
        )

        all_data: list[list[Any]] = []
        current_since = since
        remaining = limit

        while remaining > 0:
            fetch_limit = min(remaining, 1000)
            ohlcv = await exchange.fetch_ohlcv(
                symbol,
                timeframe=timeframe,
                since=current_since,
                limit=fetch_limit,
            )

            if not ohlcv:
                break

            all_data.extend(ohlcv)
            remaining -= len(ohlcv)

            # 次のページの開始位置を最後のタイムスタンプの次に設定
            current_since = ohlcv[-1][0] + 1

            if len(ohlcv) < fetch_limit:
                break

        df = pd.DataFrame(
            all_data,
            columns=["timestamp", "open", "high", "low", "close", "volume"],
        )
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)

        logger.info(
            "ヒストリカルデータを取得しました",
            symbol=symbol,
            rows=len(df),
        )

        return df

    def run(self, df: pd.DataFrame, symbol: str) -> BacktestResult:
        """バックテストを実行する。

        OHLCVデータの各バーを順にイテレートし、戦略のanalyze()を呼び出して
        シグナルを生成し、次のバーの始値で仮想的にトレードを実行する。

        Args:
            df: OHLCVデータを含むDataFrame
                (columns: timestamp, open, high, low, close, volume)
            symbol: 取引シンボル(例: "BTC/USDT")

        Returns:
            バックテスト結果
        """
        import asyncio

        logger.info(
            "バックテストを開始します",
            symbol=symbol,
            bars=len(df),
            strategy=self._strategy.name,
        )

        balance = self._initial_balance
        equity_curve: list[dict[str, Any]] = []
        trades: list[Trade] = []

        # 現在のポジション状態
        position_side: str | None = None
        position_entry_price: float = 0.0
        position_quantity: float = 0.0
        position_entry_time: str = ""
        position_entry_reason: str = ""

        # バックテスト用のポートフォリオマネージャー
        portfolio = PortfolioManager(
            initial_balance=self._initial_balance,
            max_positions=5,
            max_exposure_ratio=1.0,
            max_risk_per_trade=0.05,
        )

        # DataFrameにsymbol列を追加
        df_with_symbol = df.copy()
        df_with_symbol["symbol"] = symbol

        # 戦略に必要な最低バー数を推定(ema_long + 余裕)
        min_bars = 30

        loop = asyncio.new_event_loop()

        try:
            for i in range(min_bars, len(df) - 1):
                current_bar = df.iloc[i]
                next_bar = df.iloc[i + 1]
                timestamp_str = str(current_bar["timestamp"])

                # 現在のバーまでのデータを戦略に渡す
                historical_slice = df_with_symbol.iloc[: i + 1].copy()

                # 戦略のanalyze()を実行
                try:
                    signals = loop.run_until_complete(
                        self._strategy.analyze(historical_slice, portfolio)
                    )
                except Exception as e:
                    logger.debug(
                        "戦略の分析中にエラーが発生しました",
                        bar=i,
                        error=str(e),
                    )
                    signals = []

                # シグナルに基づいてトレードを実行(次のバーの始値で約定)
                execution_price = float(next_bar["open"])
                next_timestamp_str = str(next_bar["timestamp"])

                for signal in signals:
                    if signal.action == "hold":
                        continue

                    if signal.action == "buy" and position_side is None:
                        # 新規買いエントリー
                        # ATRの代わりに直近のボラティリティを使用してポジションサイズを計算
                        recent_data = df.iloc[max(0, i - 14) : i + 1]
                        atr = self._calculate_simple_atr(recent_data)

                        if atr <= 0:
                            atr = execution_price * 0.02

                        quantity = self._strategy.get_position_size(
                            signal, balance, atr
                        )

                        if quantity <= 0:
                            continue

                        # 注文コストの確認
                        order_cost = execution_price * quantity
                        if order_cost > balance:
                            quantity = balance / execution_price * 0.95

                        if quantity <= 0:
                            continue

                        commission = execution_price * quantity * self._commission_rate
                        balance -= commission

                        position_side = "buy"
                        position_entry_price = execution_price
                        position_quantity = quantity
                        position_entry_time = next_timestamp_str
                        position_entry_reason = signal.reason

                        logger.debug(
                            "バックテスト: 買いエントリー",
                            bar=i,
                            price=execution_price,
                            quantity=round(quantity, 6),
                        )

                    elif signal.action == "sell" and position_side == "buy":
                        # 買いポジションの決済
                        pnl_gross = (
                            execution_price - position_entry_price
                        ) * position_quantity
                        commission = (
                            execution_price * position_quantity * self._commission_rate
                        )
                        pnl_net = pnl_gross - commission

                        balance += pnl_net

                        trade = Trade(
                            entry_time=position_entry_time,
                            exit_time=next_timestamp_str,
                            symbol=symbol,
                            side="buy",
                            entry_price=position_entry_price,
                            exit_price=execution_price,
                            quantity=position_quantity,
                            pnl=pnl_net,
                            commission=commission
                            + position_entry_price
                            * position_quantity
                            * self._commission_rate,
                            reason_entry=position_entry_reason,
                            reason_exit=signal.reason,
                        )
                        trades.append(trade)

                        logger.debug(
                            "バックテスト: 売り決済",
                            bar=i,
                            price=execution_price,
                            pnl=round(pnl_net, 2),
                        )

                        position_side = None
                        position_entry_price = 0.0
                        position_quantity = 0.0
                        position_entry_time = ""
                        position_entry_reason = ""

                # 現在のエクイティを記録
                unrealized_pnl = 0.0
                if position_side == "buy":
                    unrealized_pnl = (
                        float(current_bar["close"]) - position_entry_price
                    ) * position_quantity

                equity = balance + unrealized_pnl
                equity_curve.append(
                    {
                        "timestamp": timestamp_str,
                        "equity": round(equity, 2),
                    }
                )

        finally:
            loop.close()

        # 未決済ポジションの処理(最終バーの終値で強制決済)
        if position_side == "buy" and len(df) > 0:
            last_close = float(df.iloc[-1]["close"])
            pnl_gross = (last_close - position_entry_price) * position_quantity
            commission = last_close * position_quantity * self._commission_rate
            pnl_net = pnl_gross - commission
            balance += pnl_net

            trade = Trade(
                entry_time=position_entry_time,
                exit_time=str(df.iloc[-1]["timestamp"]),
                symbol=symbol,
                side="buy",
                entry_price=position_entry_price,
                exit_price=last_close,
                quantity=position_quantity,
                pnl=pnl_net,
                commission=commission
                + position_entry_price * position_quantity * self._commission_rate,
                reason_entry=position_entry_reason,
                reason_exit="バックテスト終了による強制決済",
            )
            trades.append(trade)

        # パフォーマンス指標を計算
        result = calculate_metrics(
            equity_curve=equity_curve,
            trades=trades,
            initial_balance=self._initial_balance,
            start_date=str(df.iloc[0]["timestamp"]) if len(df) > 0 else "",
            end_date=str(df.iloc[-1]["timestamp"]) if len(df) > 0 else "",
        )

        logger.info(
            "バックテストが完了しました",
            total_return=f"{result.total_return:.2f}%",
            total_trades=result.total_trades,
            win_rate=f"{result.win_rate:.1f}%",
            max_drawdown=f"{result.max_drawdown:.2f}%",
            sharpe_ratio=f"{result.sharpe_ratio:.3f}",
        )

        return result

    @staticmethod
    def _calculate_simple_atr(df: pd.DataFrame, period: int = 14) -> float:
        """簡易ATRを計算する。

        Args:
            df: OHLCVデータを含むDataFrame
            period: ATR期間

        Returns:
            ATR値。計算不能な場合は0.0。
        """
        if len(df) < 2:
            return 0.0

        high = df["high"]
        low = df["low"]
        close = df["close"]

        tr1 = high - low
        tr2 = (high - close.shift(1)).abs()
        tr3 = (low - close.shift(1)).abs()
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

        atr = tr.ewm(span=min(period, len(df)), adjust=False).mean()

        return float(atr.iloc[-1]) if len(atr) > 0 and not pd.isna(atr.iloc[-1]) else 0.0


def calculate_metrics(
    equity_curve: list[dict[str, Any]],
    trades: list[Trade],
    initial_balance: float,
    start_date: str,
    end_date: str,
) -> BacktestResult:
    """バックテスト結果からパフォーマンス指標を算出する。

    Args:
        equity_curve: 資産推移のリスト
        trades: トレード記録のリスト
        initial_balance: 初期残高
        start_date: バックテスト開始日時
        end_date: バックテスト終了日時

    Returns:
        パフォーマンス指標を含むBacktestResult
    """
    total_trades = len(trades)

    # 合計リターン
    final_equity = equity_curve[-1]["equity"] if equity_curve else initial_balance
    total_return = ((final_equity - initial_balance) / initial_balance) * 100.0

    # 勝率
    if total_trades > 0:
        winning_trades = [t for t in trades if t.pnl > 0]
        win_rate = (len(winning_trades) / total_trades) * 100.0
    else:
        win_rate = 0.0

    # プロフィットファクター
    gross_profit = sum(t.pnl for t in trades if t.pnl > 0)
    gross_loss = abs(sum(t.pnl for t in trades if t.pnl < 0))
    if gross_loss > 0:
        profit_factor = gross_profit / gross_loss
    else:
        profit_factor = float("inf") if gross_profit > 0 else 0.0

    # 最大ドローダウン
    max_drawdown = _calculate_max_drawdown(equity_curve)

    # シャープレシオ
    sharpe_ratio = _calculate_sharpe_ratio(equity_curve)

    return BacktestResult(
        total_return=round(total_return, 4),
        sharpe_ratio=round(sharpe_ratio, 4),
        max_drawdown=round(max_drawdown, 4),
        win_rate=round(win_rate, 2),
        profit_factor=round(profit_factor, 4) if not math.isinf(profit_factor) else profit_factor,
        total_trades=total_trades,
        trades=trades,
        equity_curve=equity_curve,
        start_date=start_date,
        end_date=end_date,
    )


def _calculate_max_drawdown(equity_curve: list[dict[str, Any]]) -> float:
    """エクイティカーブから最大ドローダウンを算出する。

    Args:
        equity_curve: 資産推移のリスト

    Returns:
        最大ドローダウン(%)。データが空の場合は0.0。
    """
    if not equity_curve:
        return 0.0

    equities = [point["equity"] for point in equity_curve]
    peak = equities[0]
    max_dd = 0.0

    for equity in equities:
        if equity > peak:
            peak = equity
        if peak > 0:
            drawdown = (peak - equity) / peak * 100.0
            if drawdown > max_dd:
                max_dd = drawdown

    return max_dd


def _calculate_sharpe_ratio(
    equity_curve: list[dict[str, Any]],
    risk_free_rate: float = 0.0,
    annualization_factor: float = 365.0,
) -> float:
    """エクイティカーブからシャープレシオを算出する。

    日次リターンを基に年率換算したシャープレシオを計算する。

    Args:
        equity_curve: 資産推移のリスト
        risk_free_rate: 無リスク金利(年率、デフォルト: 0.0)
        annualization_factor: 年率換算係数(デフォルト: 365日)

    Returns:
        シャープレシオ。データ不足の場合は0.0。
    """
    if len(equity_curve) < 2:
        return 0.0

    equities = np.array([point["equity"] for point in equity_curve], dtype=float)

    # 日次リターンを計算
    returns = np.diff(equities) / equities[:-1]

    if len(returns) == 0:
        return 0.0

    mean_return = np.mean(returns)
    std_return = np.std(returns, ddof=1)

    if std_return == 0:
        return 0.0

    daily_risk_free = risk_free_rate / annualization_factor
    sharpe = (mean_return - daily_risk_free) / std_return * math.sqrt(annualization_factor)

    return float(sharpe)


def generate_report(result: BacktestResult) -> str:
    """バックテスト結果のテキストレポートを生成する。

    Args:
        result: バックテスト結果

    Returns:
        日本語のバックテストレポート文字列
    """
    separator = "=" * 60
    sub_separator = "-" * 60

    lines = [
        separator,
        "バックテストレポート",
        separator,
        "",
        f"期間: {result.start_date} - {result.end_date}",
        "",
        sub_separator,
        "パフォーマンスサマリー",
        sub_separator,
        f"  合計リターン:        {result.total_return:>10.2f} %",
        f"  シャープレシオ:      {result.sharpe_ratio:>10.4f}",
        f"  最大ドローダウン:    {result.max_drawdown:>10.2f} %",
        f"  勝率:                {result.win_rate:>10.1f} %",
        f"  プロフィットファクター: {result.profit_factor:>8.4f}"
        if not math.isinf(result.profit_factor)
        else f"  プロフィットファクター:       inf",
        f"  総トレード数:        {result.total_trades:>10d}",
        "",
    ]

    # トレード詳細
    if result.trades:
        winning = [t for t in result.trades if t.pnl > 0]
        losing = [t for t in result.trades if t.pnl <= 0]

        avg_win = sum(t.pnl for t in winning) / len(winning) if winning else 0.0
        avg_loss = sum(t.pnl for t in losing) / len(losing) if losing else 0.0
        max_win = max((t.pnl for t in result.trades), default=0.0)
        max_loss = min((t.pnl for t in result.trades), default=0.0)
        total_commission = sum(t.commission for t in result.trades)

        lines.extend([
            sub_separator,
            "トレード統計",
            sub_separator,
            f"  勝ちトレード数:      {len(winning):>10d}",
            f"  負けトレード数:      {len(losing):>10d}",
            f"  平均利益:            {avg_win:>10.2f} USDT",
            f"  平均損失:            {avg_loss:>10.2f} USDT",
            f"  最大利益:            {max_win:>10.2f} USDT",
            f"  最大損失:            {max_loss:>10.2f} USDT",
            f"  手数料合計:          {total_commission:>10.2f} USDT",
            "",
        ])

        # 直近5件のトレード
        lines.extend([
            sub_separator,
            "直近トレード (最大5件)",
            sub_separator,
        ])
        recent_trades = result.trades[-5:]
        for i, trade in enumerate(recent_trades, 1):
            pnl_label = "利益" if trade.pnl >= 0 else "損失"
            lines.extend([
                f"  [{i}] {trade.symbol} {trade.side.upper()}",
                f"      エントリー: {trade.entry_price:.2f} ({trade.entry_time})",
                f"      決済:       {trade.exit_price:.2f} ({trade.exit_time})",
                f"      {pnl_label}:       {trade.pnl:+.2f} USDT",
                "",
            ])

    lines.append(separator)

    return "\n".join(lines)
