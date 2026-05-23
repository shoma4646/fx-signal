import pandas as pd

from fx_signal.backtest.runner import run
from fx_signal.config import SignalConfig


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


def _cfg() -> SignalConfig:
    return SignalConfig(
        ema_short=3,
        ema_long=5,
        rsi_period=3,
        rsi_buy_threshold=50.0,
        rsi_sell_threshold=50.0,
        adx_period=3,
        adx_threshold=0.0,
    )


def test_no_trades_returns_zero_result():
    closes = [100.0 + i * 0.01 for i in range(30)]
    df = _make_df(closes)
    result = run(df, _cfg())
    assert result.total_trades == 0
    assert result.win_rate == 0.0
    assert result.total_return_pct == 0.0


def test_result_summary_contains_key_metrics():
    closes = [100.0 + i * 0.01 for i in range(50)]
    df = _make_df(closes)
    result = run(df, _cfg())
    summary = result.summary()
    assert "勝率" in summary
    assert "総リターン" in summary
    assert "最大ドローダウン" in summary
    assert "シャープレシオ" in summary
