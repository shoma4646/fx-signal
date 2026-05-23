from dataclasses import dataclass
from zoneinfo import ZoneInfo

import pandas as pd
import pandas_ta as ta

from fx_signal.config import SignalConfig

_JST = ZoneInfo("Asia/Tokyo")
_DEAD_HOURS = frozenset({5, 6, 7, 8})

# USD/JPYの1pip = 0.01円
_JPY_PIP_SIZE = 0.01


@dataclass
class BacktestResult:
    total_trades: int
    wins: int
    losses: int
    win_rate: float
    total_return_pct: float
    max_drawdown_pct: float
    sharpe_ratio: float
    spread_cost_pct: float

    def summary(self) -> str:
        lines = [
            "=== バックテスト結果 ===",
            f"総トレード数: {self.total_trades}",
            f"勝率: {self.win_rate:.1%}",
            f"総リターン: {self.total_return_pct:+.2f}%",
            f"最大ドローダウン: {self.max_drawdown_pct:.2f}%",
            f"シャープレシオ(年率): {self.sharpe_ratio:.2f}",
            f"スプレッドコスト合計: {self.spread_cost_pct:.3f}%",
        ]
        return "\n".join(lines)


def run(df: pd.DataFrame, cfg: SignalConfig) -> BacktestResult:
    """EMAクロス戦略のバックテストを実行する。

    エントリ: EMAゴールデンクロス + RSIフィルター + ADXフィルター
    エグジット: EMAデッドクロス
    スプレッド・セッションフィルターを考慮した現実的な損益を計算する。
    """
    df = df.copy()
    df["ema_short"] = ta.ema(df["close"], length=cfg.ema_short)
    df["ema_long"] = ta.ema(df["close"], length=cfg.ema_long)
    df["rsi"] = ta.rsi(df["close"], length=cfg.rsi_period)

    adx_result = ta.adx(df["high"], df["low"], df["close"], length=cfg.adx_period)
    adx_col = f"ADX_{cfg.adx_period}"
    df["adx"] = adx_result[adx_col] if adx_result is not None and adx_col in adx_result.columns else float("nan")
    df = df.dropna()

    # タイムスタンプをJSTに変換してセッション判定用のhour列を追加
    if cfg.session_filter:
        idx_jst = pd.to_datetime(df.index).tz_convert(_JST) if df.index.tzinfo else pd.to_datetime(df.index)
        df["hour_jst"] = idx_jst.hour

    in_position = False
    entry_price = 0.0
    trades: list[float] = []
    equity_curve: list[float] = [1.0]
    total_spread_cost = 0.0

    for i in range(1, len(df)):
        prev = df.iloc[i - 1]
        curr = df.iloc[i]

        # セッションフィルター: 低ボラ時間帯はスキップ
        if cfg.session_filter and int(curr["hour_jst"]) in _DEAD_HOURS:
            continue

        adx_ok = float(curr["adx"]) >= cfg.adx_threshold

        golden = (
            float(prev["ema_short"]) <= float(prev["ema_long"])
            and float(curr["ema_short"]) > float(curr["ema_long"])
        )
        dead = (
            float(prev["ema_short"]) >= float(prev["ema_long"])
            and float(curr["ema_short"]) < float(curr["ema_long"])
        )

        if not in_position and golden and float(curr["rsi"]) >= cfg.rsi_buy_threshold and adx_ok:
            in_position = True
            entry_price = float(curr["close"])

        elif in_position and dead:
            exit_price = float(curr["close"])
            # 往復スプレッドコスト（pips → 円 → 比率）
            spread_cost = (cfg.spread_pips * _JPY_PIP_SIZE * 2) / entry_price
            pnl_pct = (exit_price - entry_price) / entry_price - spread_cost
            trades.append(pnl_pct)
            equity_curve.append(equity_curve[-1] * (1 + pnl_pct))
            total_spread_cost += spread_cost
            in_position = False

    if not trades:
        return BacktestResult(0, 0, 0, 0.0, 0.0, 0.0, 0.0, 0.0)

    wins = sum(1 for t in trades if t > 0)
    losses = len(trades) - wins
    total_return = (equity_curve[-1] - 1.0) * 100

    equity_series = pd.Series(equity_curve)
    peak = equity_series.cummax()
    drawdown = (equity_series - peak) / peak
    max_dd = float(drawdown.min()) * 100

    returns_series = pd.Series(trades)
    bars_per_year = {"1h": 8760, "4h": 2190, "1d": 252}.get(cfg.interval, 8760)
    if returns_series.std() > 0:
        sharpe = (returns_series.mean() / returns_series.std()) * (bars_per_year**0.5)
    else:
        sharpe = 0.0

    return BacktestResult(
        total_trades=len(trades),
        wins=wins,
        losses=losses,
        win_rate=wins / len(trades),
        total_return_pct=total_return,
        max_drawdown_pct=max_dd,
        sharpe_ratio=float(sharpe),
        spread_cost_pct=total_spread_cost * 100,
    )
