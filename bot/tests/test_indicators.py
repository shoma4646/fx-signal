"""テクニカル指標計算のユニットテスト。

EMA、ADX、ATR、RSIの計算結果が妥当な範囲にあることを検証する。
pandas-taの計算結果を利用するため、値の正確性よりも
入出力の整合性と妥当性に重点を置いたテストとする。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from stella.indicators.technical import TechnicalIndicators


@pytest.fixture
def indicators() -> TechnicalIndicators:
    """TechnicalIndicatorsインスタンスを生成する。"""
    return TechnicalIndicators(cache_ttl_sec=60)


@pytest.fixture
def ohlcv_df() -> pd.DataFrame:
    """テスト用のOHLCVデータ(100行)を生成する。

    上昇トレンドのデータを生成し、各指標が有意な値を持つようにする。
    """
    np.random.seed(42)
    n = 100
    base = np.linspace(100, 150, n)
    noise = np.random.normal(0, 1, n)
    close = base + noise
    high = close + np.abs(np.random.normal(1, 0.5, n))
    low = close - np.abs(np.random.normal(1, 0.5, n))
    open_ = close + np.random.normal(0, 0.5, n)
    volume = np.random.uniform(1000, 5000, n)

    return pd.DataFrame({
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
    })


class TestEMACalculation:
    """EMA(指数移動平均)計算のテスト。"""

    def test_ema_returns_series_of_correct_length(
        self, indicators: TechnicalIndicators, ohlcv_df: pd.DataFrame
    ) -> None:
        """EMAの結果がDataFrameと同じ長さのSeriesであること。"""
        ema = indicators.calculate_ema(ohlcv_df, period=9)

        assert isinstance(ema, pd.Series)
        assert len(ema) == len(ohlcv_df)

    def test_ema_initial_values_are_nan(
        self, indicators: TechnicalIndicators, ohlcv_df: pd.DataFrame
    ) -> None:
        """EMAの先頭(ウォームアップ期間)にNaN値が含まれること。"""
        ema = indicators.calculate_ema(ohlcv_df, period=20)

        # pandas-taのEMAは最初のperiod-1個がNaN
        assert pd.isna(ema.iloc[0])

    def test_ema_latest_value_close_to_price(
        self, indicators: TechnicalIndicators, ohlcv_df: pd.DataFrame
    ) -> None:
        """EMAの最新値が直近の価格と大きく乖離しないこと。"""
        ema = indicators.calculate_ema(ohlcv_df, period=9)
        latest_close = ohlcv_df["close"].iloc[-1]
        latest_ema = ema.iloc[-1]

        # 短期EMAは直近価格から大きく離れない
        assert abs(latest_ema - latest_close) < latest_close * 0.1

    def test_short_ema_more_responsive_than_long(
        self, indicators: TechnicalIndicators, ohlcv_df: pd.DataFrame
    ) -> None:
        """短期EMAは長期EMAより直近の価格変動に敏感であること。"""
        ema_short = indicators.calculate_ema(ohlcv_df, period=9)
        ema_long = indicators.calculate_ema(ohlcv_df, period=21)
        latest_close = ohlcv_df["close"].iloc[-1]

        # 上昇トレンドでは短期EMAの方が直近価格に近い
        short_diff = abs(ema_short.iloc[-1] - latest_close)
        long_diff = abs(ema_long.iloc[-1] - latest_close)
        assert short_diff < long_diff


class TestADXCalculation:
    """ADX(Average Directional Index)計算のテスト。"""

    def test_adx_returns_dataframe(
        self, indicators: TechnicalIndicators, ohlcv_df: pd.DataFrame
    ) -> None:
        """ADXの結果がDataFrameであること。"""
        adx_df = indicators.calculate_adx(ohlcv_df, period=14)

        assert isinstance(adx_df, pd.DataFrame)

    def test_adx_contains_required_columns(
        self, indicators: TechnicalIndicators, ohlcv_df: pd.DataFrame
    ) -> None:
        """ADXの結果にADX、DI+、DI-のカラムが含まれること。"""
        adx_df = indicators.calculate_adx(ohlcv_df, period=14)

        assert "ADX_14" in adx_df.columns
        assert "DMP_14" in adx_df.columns
        assert "DMN_14" in adx_df.columns

    def test_adx_values_in_valid_range(
        self, indicators: TechnicalIndicators, ohlcv_df: pd.DataFrame
    ) -> None:
        """ADXの有効な値が0から100の範囲にあること。"""
        adx_df = indicators.calculate_adx(ohlcv_df, period=14)
        adx_values = adx_df["ADX_14"].dropna()

        assert (adx_values >= 0).all()
        assert (adx_values <= 100).all()

    def test_adx_positive_in_trending_market(
        self, indicators: TechnicalIndicators, ohlcv_df: pd.DataFrame
    ) -> None:
        """上昇トレンドのデータでADXが正の値を持つこと。"""
        adx_df = indicators.calculate_adx(ohlcv_df, period=14)
        latest_adx = adx_df["ADX_14"].dropna().iloc[-1]

        # 明確な上昇トレンドなのでADXは0より大きい
        assert latest_adx > 0


class TestATRCalculation:
    """ATR(Average True Range)計算のテスト。"""

    def test_atr_returns_series(
        self, indicators: TechnicalIndicators, ohlcv_df: pd.DataFrame
    ) -> None:
        """ATRの結果がSeriesであること。"""
        atr = indicators.calculate_atr(ohlcv_df, period=14)

        assert isinstance(atr, pd.Series)

    def test_atr_values_are_positive(
        self, indicators: TechnicalIndicators, ohlcv_df: pd.DataFrame
    ) -> None:
        """ATRの有効な値が全て正であること。"""
        atr = indicators.calculate_atr(ohlcv_df, period=14)
        valid_values = atr.dropna()

        assert (valid_values > 0).all()

    def test_atr_reflects_volatility(self, indicators: TechnicalIndicators) -> None:
        """ボラティリティが高いデータの方がATRが大きくなること。"""
        np.random.seed(42)
        n = 100

        # 低ボラティリティデータ
        close_low = np.linspace(100, 110, n) + np.random.normal(0, 0.1, n)
        df_low = pd.DataFrame({
            "open": close_low,
            "high": close_low + 0.2,
            "low": close_low - 0.2,
            "close": close_low,
            "volume": np.full(n, 1000.0),
        })

        # 高ボラティリティデータ
        close_high = np.linspace(100, 110, n) + np.random.normal(0, 5, n)
        df_high = pd.DataFrame({
            "open": close_high,
            "high": close_high + 10,
            "low": close_high - 10,
            "close": close_high,
            "volume": np.full(n, 1000.0),
        })

        atr_low = indicators.calculate_atr(df_low, period=14).dropna().iloc[-1]
        atr_high = indicators.calculate_atr(df_high, period=14).dropna().iloc[-1]

        assert atr_high > atr_low


class TestRSICalculation:
    """RSI(Relative Strength Index)計算のテスト。"""

    def test_rsi_returns_series(
        self, indicators: TechnicalIndicators, ohlcv_df: pd.DataFrame
    ) -> None:
        """RSIの結果がSeriesであること。"""
        rsi = indicators.calculate_rsi(ohlcv_df, period=14)

        assert isinstance(rsi, pd.Series)

    def test_rsi_values_in_valid_range(
        self, indicators: TechnicalIndicators, ohlcv_df: pd.DataFrame
    ) -> None:
        """RSIの有効な値が0から100の範囲にあること。"""
        rsi = indicators.calculate_rsi(ohlcv_df, period=14)
        valid_values = rsi.dropna()

        assert (valid_values >= 0).all()
        assert (valid_values <= 100).all()

    def test_rsi_overbought_in_strong_uptrend(
        self, indicators: TechnicalIndicators
    ) -> None:
        """急激な上昇トレンドでRSIが買われすぎ(70以上)領域に入ること。"""
        np.random.seed(42)
        n = 100
        # 急上昇データ
        close = np.linspace(100, 200, n)
        df = pd.DataFrame({
            "open": close - 0.5,
            "high": close + 1,
            "low": close - 1,
            "close": close,
            "volume": np.full(n, 1000.0),
        })

        rsi = indicators.calculate_rsi(df, period=14)
        latest_rsi = rsi.dropna().iloc[-1]

        assert latest_rsi > 70

    def test_rsi_oversold_in_strong_downtrend(
        self, indicators: TechnicalIndicators
    ) -> None:
        """急激な下降トレンドでRSIが売られすぎ(30以下)領域に入ること。"""
        np.random.seed(42)
        n = 100
        # 急下降データ
        close = np.linspace(200, 100, n)
        df = pd.DataFrame({
            "open": close + 0.5,
            "high": close + 1,
            "low": close - 1,
            "close": close,
            "volume": np.full(n, 1000.0),
        })

        rsi = indicators.calculate_rsi(df, period=14)
        latest_rsi = rsi.dropna().iloc[-1]

        assert latest_rsi < 30

    def test_rsi_near_50_in_flat_market(self, indicators: TechnicalIndicators) -> None:
        """横ばい相場でRSIが50付近にあること。"""
        np.random.seed(42)
        n = 200
        # ランダムウォーク(トレンドなし)
        changes = np.random.choice([-1, 1], size=n)
        close = 100.0 + np.cumsum(changes * 0.5)
        df = pd.DataFrame({
            "open": close,
            "high": close + 0.5,
            "low": close - 0.5,
            "close": close,
            "volume": np.full(n, 1000.0),
        })

        rsi = indicators.calculate_rsi(df, period=14)
        latest_rsi = rsi.dropna().iloc[-1]

        # 横ばいでは50付近(30-70の範囲内)にいるはず
        assert 30 <= latest_rsi <= 70


class TestCache:
    """OHLCVキャッシュのテスト。"""

    def test_cache_hit(self, indicators: TechnicalIndicators, ohlcv_df: pd.DataFrame) -> None:
        """キャッシュに保存したデータが取得できること。"""
        indicators.cache_ohlcv("BTC/USDT", "1h", ohlcv_df)
        cached = indicators.get_cached_ohlcv("BTC/USDT", "1h")

        assert cached is not None
        assert len(cached) == len(ohlcv_df)

    def test_cache_miss(self, indicators: TechnicalIndicators) -> None:
        """キャッシュにないデータがNoneを返すこと。"""
        cached = indicators.get_cached_ohlcv("ETH/USDT", "1h")

        assert cached is None

    def test_cache_invalidation(
        self, indicators: TechnicalIndicators, ohlcv_df: pd.DataFrame
    ) -> None:
        """キャッシュ無効化後にNoneが返ること。"""
        indicators.cache_ohlcv("BTC/USDT", "1h", ohlcv_df)
        indicators.invalidate_cache("BTC/USDT:1h")

        cached = indicators.get_cached_ohlcv("BTC/USDT", "1h")
        assert cached is None

    def test_cache_invalidate_all(
        self, indicators: TechnicalIndicators, ohlcv_df: pd.DataFrame
    ) -> None:
        """全キャッシュの無効化が正しく動作すること。"""
        indicators.cache_ohlcv("BTC/USDT", "1h", ohlcv_df)
        indicators.cache_ohlcv("ETH/USDT", "1h", ohlcv_df)
        indicators.invalidate_cache()

        assert indicators.get_cached_ohlcv("BTC/USDT", "1h") is None
        assert indicators.get_cached_ohlcv("ETH/USDT", "1h") is None
