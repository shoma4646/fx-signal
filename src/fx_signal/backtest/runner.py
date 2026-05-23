from dataclasses import dataclass
from zoneinfo import ZoneInfo

import pandas as pd
import pandas_ta as ta

from fx_signal.config import SignalConfig

_JST = ZoneInfo("Asia/Tokyo")
_DEAD_HOURS = frozenset({5, 6, 7, 8})
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
    avg_rr: float

    def summary(self) -> str:
        lines = [
            "=== バックテスト結果 (RSI逆張り + ATR TP/SL) ===",
            f"総トレード数    : {self.total_trades}",
            f"勝率            : {self.win_rate:.1%}",
            f"総リターン      : {self.total_return_pct:+.2f}%",
            f"最大ドローダウン: {self.max_drawdown_pct:.2f}%",
            f"シャープレシオ  : {self.sharpe_ratio:.2f}",
            f"平均R:R比       : 1:{self.avg_rr:.2f}",
            f"スプレッドコスト: {self.spread_cost_pct:.3f}%",
        ]
        return "\n".join(lines)


def run(df: pd.DataFrame, cfg: SignalConfig) -> BacktestResult:
    """RSI逆張り + ATRベースTP/SLのバックテストを実行する。

    エントリ : RSI < rsi_oversold (買い) / RSI > rsi_overbought (売り)
    エグジット: TP/SLにどちらが先に到達したかを高値・安値で判定する
    """
    df = df.copy()
    df["rsi"] = ta.rsi(df["close"], length=cfg.rsi_period)
    df["atr"] = ta.atr(df["high"], df["low"], df["close"], length=cfg.atr_period)
    df = df.dropna()

    idx_jst = pd.to_datetime(df.index).tz_convert(_JST) if df.index.tzinfo else pd.to_datetime(df.index)
    hours = idx_jst.hour

    in_position = False
    is_long = True
    entry_price = sl = 0.0
    trades: list[float] = []
    equity: list[float] = [1.0]
    total_spread = 0.0
    rr_list: list[float] = []

    for i in range(len(df)):
        if cfg.session_filter and hours[i] in _DEAD_HOURS:
            continue

        curr = df.iloc[i]
        high = float(curr["high"])
        low = float(curr["low"])
        price = float(curr["close"])
        rsi_val = float(curr["rsi"])
        atr_val = float(curr["atr"])
        spread_cost = (cfg.spread_pips * _JPY_PIP_SIZE * 2) / price

        if in_position:
            # SLは安全網（ワイド: ATR×3）として高値・安値で判定
            hit_sl = (low <= sl) if is_long else (high >= sl)
            # メイン出口: 逆のRSI極値に達したら決済（元の比較と一致する出口）
            rsi_exit = (rsi_val >= cfg.rsi_overbought) if is_long else (rsi_val <= cfg.rsi_oversold)

            if hit_sl or rsi_exit:
                exit_price = sl if hit_sl else price
                pnl = (exit_price - entry_price) / entry_price * (1 if is_long else -1) - spread_cost
                trades.append(pnl)
                equity.append(equity[-1] * (1 + pnl))
                total_spread += spread_cost
                sl_dist = cfg.sl_atr_mult * atr_val
                rr_list.append(abs(exit_price - entry_price) / sl_dist if sl_dist > 0 else 0)
                in_position = False

        if not in_position:
            if rsi_val < cfg.rsi_oversold:
                in_position = True
                is_long = True
                entry_price = price
                # SLはワイド（ATR×3）で安全網として機能させる
                sl = price - atr_val * 3.0
            elif rsi_val > cfg.rsi_overbought:
                in_position = True
                is_long = False
                entry_price = price
                sl = price + atr_val * 3.0

    if not trades:
        return BacktestResult(0, 0, 0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)

    wins = sum(1 for t in trades if t > 0)
    total_ret = (equity[-1] - 1.0) * 100
    eq_s = pd.Series(equity)
    max_dd = float(((eq_s - eq_s.cummax()) / eq_s.cummax()).min()) * 100
    ret_s = pd.Series(trades)
    bars_per_year = {"1h": 8760, "4h": 2190, "1d": 252}.get(cfg.interval, 8760)
    sharpe = (ret_s.mean() / ret_s.std() * (bars_per_year ** 0.5)) if ret_s.std() > 0 else 0.0

    return BacktestResult(
        total_trades=len(trades),
        wins=wins,
        losses=len(trades) - wins,
        win_rate=wins / len(trades),
        total_return_pct=total_ret,
        max_drawdown_pct=max_dd,
        sharpe_ratio=float(sharpe),
        spread_cost_pct=total_spread * 100,
        avg_rr=float(sum(rr_list) / len(rr_list)) if rr_list else 0.0,
    )
