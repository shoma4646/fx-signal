from datetime import datetime, timedelta

import pandas as pd
import pandas_ta as ta
import yfinance as yf


def fetch_ohlcv(pair: str, interval: str, lookback_days: int) -> pd.DataFrame:
    """yfinanceからOHLCVデータを取得する。"""
    end = datetime.now()
    start = end - timedelta(days=lookback_days)

    df = yf.download(
        pair,
        start=start.strftime("%Y-%m-%d"),
        end=end.strftime("%Y-%m-%d"),
        interval=interval,
        auto_adjust=True,
        progress=False,
    )

    if df.empty:
        raise ValueError(f"{pair} のデータ取得に失敗しました")

    # MultiIndex列をフラット化
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    df.columns = [c.lower() for c in df.columns]
    df = df[["open", "high", "low", "close", "volume"]].dropna()
    df.index = pd.to_datetime(df.index)

    return df


def get_trend_direction(pair: str) -> str:
    """4H足のEMA20でトレンド方向を返す（上昇/下降/横ばい）。"""
    df = fetch_ohlcv(pair, "4h", 30)
    ema = ta.ema(df["close"], length=20).dropna()
    if len(ema) < 2:
        return "横ばい"
    slope = float(ema.iloc[-1]) - float(ema.iloc[-3])  # 直近3本の変化で判定
    if slope > 0.05:
        return "上昇トレンド"
    elif slope < -0.05:
        return "下降トレンド"
    return "横ばい"
