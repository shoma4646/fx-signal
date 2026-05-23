import pandas as pd
from datetime import datetime

from fx_signal.config import SignalConfig
from fx_signal.signals.base import Direction, Signal
from fx_signal.signals.rsi_reversion import detect


def _make_df(closes: list[float]) -> pd.DataFrame:
    idx = pd.date_range("2024-01-01 10:00", periods=len(closes), freq="1h", tz="UTC")
    close = pd.Series(closes, index=idx)
    return pd.DataFrame({
        "open": close,
        "high": close * 1.002,
        "low": close * 0.998,
        "close": close,
        "volume": [1000.0] * len(closes),
    })


def _cfg(**kwargs) -> SignalConfig:
    defaults = dict(rsi_period=3, atr_period=3, session_filter=False)
    defaults.update(kwargs)
    return SignalConfig(**defaults)


def test_buy_signal_when_oversold():
    closes = [100.0] * 20 + [90.0, 88.0, 86.0, 84.0, 82.0]
    df = _make_df(closes)
    signal = detect(df, _cfg())
    assert signal is not None
    assert signal.direction == Direction.BUY


def test_sell_signal_when_overbought():
    closes = [100.0] * 20 + [110.0, 112.0, 114.0, 116.0, 118.0]
    df = _make_df(closes)
    signal = detect(df, _cfg())
    assert signal is not None
    assert signal.direction == Direction.SELL


def test_no_signal_in_neutral_rsi():
    # 上下を繰り返す → RSIが中立付近に留まる
    closes = [100.0 + (i % 2) * 0.1 for i in range(50)]
    df = _make_df(closes)
    assert detect(df, _cfg()) is None


def test_buy_signal_has_tp_above_sl_below():
    closes = [100.0] * 20 + [90.0, 88.0, 86.0, 84.0, 82.0]
    df = _make_df(closes)
    signal = detect(df, _cfg())
    assert signal is not None and signal.tp is not None and signal.sl is not None
    assert signal.tp > signal.price
    assert signal.sl < signal.price


def test_sell_signal_has_tp_below_sl_above():
    closes = [100.0] * 20 + [110.0, 112.0, 114.0, 116.0, 118.0]
    df = _make_df(closes)
    signal = detect(df, _cfg())
    assert signal is not None and signal.tp is not None and signal.sl is not None
    assert signal.tp < signal.price
    assert signal.sl > signal.price


def test_notification_contains_tp_sl():
    s = Signal(
        Direction.BUY, "USDJPY=X", 155.0,
        datetime(2024, 1, 1, 10, 0),
        "テスト",
        tp=155.5, sl=154.7,
    )
    title, body = s.to_notification()
    assert "買い" in title
    assert "155.500" in body
    assert "154.700" in body
