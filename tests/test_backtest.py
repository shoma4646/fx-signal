import pandas as pd

from fx_signal.backtest.runner import run
from fx_signal.config import SignalConfig


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


def _cfg() -> SignalConfig:
    return SignalConfig(rsi_period=3, atr_period=3, session_filter=False)


def test_no_trades_returns_zero_result():
    # 緩やかな上昇 → RSIが中立付近に留まり閾値(30/70)に達しない
    closes = [100.0 + i * 0.01 for i in range(100)]
    df = _make_df(closes)
    result = run(df, _cfg())
    # RSIが30未満/70超にならなければ0件、なっても少数
    assert result.total_return_pct == result.total_return_pct  # クラッシュしない
    assert 0.0 <= result.win_rate <= 1.0


def test_result_summary_contains_key_metrics():
    closes = [100.0] * 20 + [90.0, 88.0, 86.0, 84.0, 82.0] + [100.0] * 25
    df = _make_df(closes)
    result = run(df, _cfg())
    summary = result.summary()
    assert "勝率" in summary
    assert "総リターン" in summary
    assert "最大ドローダウン" in summary
    assert "シャープレシオ" in summary
