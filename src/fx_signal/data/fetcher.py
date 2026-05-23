from datetime import datetime, timedelta

import pandas as pd
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
