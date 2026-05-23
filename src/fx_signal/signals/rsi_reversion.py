from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import pandas_ta as ta

from fx_signal.config import SignalConfig
from fx_signal.signals.base import Direction, Signal

_JST = ZoneInfo("Asia/Tokyo")
_DEAD_HOURS = frozenset({5, 6, 7, 8})


def detect(df: pd.DataFrame, cfg: SignalConfig) -> Signal | None:
    """RSI逆張り戦略でシグナルを検出し、ATRベースのTP/SLを付与する。

    買い: RSI < rsi_oversold (デフォルト30) → 売られすぎからの反発を狙う
    売り: RSI > rsi_overbought (デフォルト70) → 買われすぎからの反落を狙う
    TP = ATR × tp_atr_mult / SL = ATR × sl_atr_mult
    """
    rsi = ta.rsi(df["close"], length=cfg.rsi_period)
    atr = ta.atr(df["high"], df["low"], df["close"], length=cfg.atr_period)

    valid = rsi.notna() & atr.notna()
    if valid.sum() < 2:
        return None

    curr_idx = df.index[-1]
    curr_rsi = float(rsi.iloc[-1])
    curr_atr = float(atr.iloc[-1])
    price = float(df["close"].iloc[-1])
    ts = curr_idx.to_pydatetime() if hasattr(curr_idx, "to_pydatetime") else datetime.now()

    if cfg.session_filter:
        ts_jst = ts.astimezone(_JST) if ts.tzinfo else ts
        if ts_jst.hour in _DEAD_HOURS:
            return None

    sl_dist = curr_atr * cfg.sl_atr_mult
    tp_dist = curr_atr * cfg.tp_atr_mult
    rr = cfg.tp_atr_mult / cfg.sl_atr_mult

    if curr_rsi < cfg.rsi_oversold:
        reason = (
            f"RSI={curr_rsi:.1f}(<{cfg.rsi_oversold:.0f}) 売られすぎ, "
            f"ATR={curr_atr:.3f}, R:R=1:{rr:.1f}"
        )
        return Signal(
            direction=Direction.BUY,
            pair=cfg.pair,
            price=price,
            timestamp=ts,
            reason=reason,
            tp=round(price + tp_dist, 3),
            sl=round(price - sl_dist, 3),
        )

    if curr_rsi > cfg.rsi_overbought:
        reason = (
            f"RSI={curr_rsi:.1f}(>{cfg.rsi_overbought:.0f}) 買われすぎ, "
            f"ATR={curr_atr:.3f}, R:R=1:{rr:.1f}"
        )
        return Signal(
            direction=Direction.SELL,
            pair=cfg.pair,
            price=price,
            timestamp=ts,
            reason=reason,
            tp=round(price - tp_dist, 3),
            sl=round(price + sl_dist, 3),
        )

    return None
