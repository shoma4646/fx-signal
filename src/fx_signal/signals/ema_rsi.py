from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import pandas_ta as ta

from fx_signal.config import SignalConfig
from fx_signal.signals.base import Direction, Signal

_JST = ZoneInfo("Asia/Tokyo")
# ボラティリティ分析で特定した低流動性時間帯（JST）
_DEAD_HOURS = frozenset({5, 6, 7, 8})


def _in_dead_zone(ts: datetime) -> bool:
    """バーのタイムスタンプが低ボラ時間帯（JST 5-8時）かどうかを返す。"""
    ts_jst = ts.astimezone(_JST) if ts.tzinfo else ts
    return ts_jst.hour in _DEAD_HOURS


def detect(df: pd.DataFrame, cfg: SignalConfig) -> Signal | None:
    """EMAクロス + RSIフィルター + ADXトレンド確認でシグナルを検出する。

    直近2本のバーを使ってクロスを判定する。
    セッションフィルターが有効な場合、低ボラ時間帯（JST 5-8時）は除外する。
    """
    df = df.copy()
    df["ema_short"] = ta.ema(df["close"], length=cfg.ema_short)
    df["ema_long"] = ta.ema(df["close"], length=cfg.ema_long)
    df["rsi"] = ta.rsi(df["close"], length=cfg.rsi_period)

    adx_result = ta.adx(df["high"], df["low"], df["close"], length=cfg.adx_period)
    adx_col = f"ADX_{cfg.adx_period}"
    if adx_result is not None and adx_col in adx_result.columns:
        df["adx"] = adx_result[adx_col]
    else:
        df["adx"] = pd.Series(dtype=float)

    df = df.dropna()
    if len(df) < 2:
        return None

    prev = df.iloc[-2]
    curr = df.iloc[-1]
    price = float(curr["close"])
    ts = curr.name.to_pydatetime() if hasattr(curr.name, "to_pydatetime") else datetime.now()

    if cfg.session_filter and _in_dead_zone(ts):
        return None

    adx_ok = float(curr["adx"]) >= cfg.adx_threshold if not pd.isna(curr["adx"]) else True

    # ゴールデンクロス（短期が長期を上抜け）
    golden_cross = (
        float(prev["ema_short"]) <= float(prev["ema_long"])
        and float(curr["ema_short"]) > float(curr["ema_long"])
    )
    if golden_cross and float(curr["rsi"]) >= cfg.rsi_buy_threshold and adx_ok:
        reason = (
            f"EMAゴールデンクロス(短期{cfg.ema_short}/長期{cfg.ema_long}), "
            f"RSI={curr['rsi']:.1f}(>{cfg.rsi_buy_threshold}), "
            f"ADX={curr['adx']:.1f}(>{cfg.adx_threshold})"
        )
        return Signal(Direction.BUY, cfg.pair, price, ts, reason)

    # デッドクロス（短期が長期を下抜け）
    dead_cross = (
        float(prev["ema_short"]) >= float(prev["ema_long"])
        and float(curr["ema_short"]) < float(curr["ema_long"])
    )
    if dead_cross and float(curr["rsi"]) <= cfg.rsi_sell_threshold and adx_ok:
        reason = (
            f"EMAデッドクロス(短期{cfg.ema_short}/長期{cfg.ema_long}), "
            f"RSI={curr['rsi']:.1f}(<{cfg.rsi_sell_threshold}), "
            f"ADX={curr['adx']:.1f}(>{cfg.adx_threshold})"
        )
        return Signal(Direction.SELL, cfg.pair, price, ts, reason)

    return None
