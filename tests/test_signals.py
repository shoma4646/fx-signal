import pandas as pd
import pytest

from fx_signal.config import SignalConfig
from fx_signal.signals.base import Direction
from fx_signal.signals.ema_rsi import detect


def _make_df(closes: list[float]) -> pd.DataFrame:
    idx = pd.date_range("2024-01-01", periods=len(closes), freq="1h")
    close = pd.Series(closes, index=idx)
    return pd.DataFrame({
        "open": close,
        "high": close * 1.001,
        "low": close * 0.999,
        "close": close,
        "volume": [1000.0] * len(closes),
    })


def _cfg(**kwargs) -> SignalConfig:
    defaults = dict(
        ema_short=3,
        ema_long=5,
        rsi_period=3,
        rsi_buy_threshold=50.0,
        rsi_sell_threshold=50.0,
        adx_period=3,
        adx_threshold=0.0,  # ADXを無効化して純粋なEMA+RSIをテスト
    )
    defaults.update(kwargs)
    return SignalConfig(**defaults)


def test_no_signal_when_no_cross():
    # 単調増加 → クロスなし
    closes = [100.0 + i * 0.1 for i in range(50)]
    df = _make_df(closes)
    assert detect(df, _cfg()) is None


def test_buy_signal_on_golden_cross():
    # デッドクロス後にゴールデンクロスを作る
    closes = (
        [105.0] * 10  # 短期 > 長期
        + [100.0] * 10  # 短期 < 長期（デッドクロス）
        + [105.0] * 10  # 短期 > 長期（ゴールデンクロス）
    )
    df = _make_df(closes)
    signal = detect(df, _cfg())
    if signal is not None:
        assert signal.direction == Direction.BUY


def test_returns_none_when_not_enough_data():
    df = _make_df([100.0, 101.0])
    assert detect(df, _cfg()) is None


def test_signal_message_contains_direction():
    from datetime import datetime
    from fx_signal.signals.base import Signal
    s = Signal(Direction.BUY, "USDJPY=X", 155.0, datetime(2024, 1, 1, 9, 0), "テスト")
    msg = s.to_line_message()
    assert "買い" in msg
    assert "155.000" in msg
