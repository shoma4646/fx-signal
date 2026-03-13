"""
テクニカル指標計算モジュール

pandas-taを使用してOHLCVデータからテクニカル指標を計算する。
TTLベースのキャッシュ機構により、短期間の重複計算を防止する。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import pandas as pd
import pandas_ta as ta
import structlog

logger = structlog.get_logger()

# キャッシュのデフォルトTTL（秒）
DEFAULT_CACHE_TTL_SEC = 60


@dataclass
class _CacheEntry:
    """キャッシュエントリ

    Attributes:
        data: キャッシュされたDataFrame
        created_at: キャッシュ作成時刻（monotonic）
    """

    data: pd.DataFrame
    created_at: float


class TechnicalIndicators:
    """テクニカル指標の計算クラス

    pandas-taを使用してEMA、ADX、ATR、RSI、MACD、ボリンジャーバンドなどの
    テクニカル指標を計算する。OHLCVデータのTTLベースキャッシュ機構を含む。
    """

    def __init__(self, cache_ttl_sec: int = DEFAULT_CACHE_TTL_SEC) -> None:
        """TechnicalIndicatorsを初期化する

        Args:
            cache_ttl_sec: OHLCVキャッシュの有効期間（秒）
        """
        self._cache_ttl_sec = cache_ttl_sec
        self._cache: dict[str, _CacheEntry] = {}

    def _get_cached(self, key: str) -> pd.DataFrame | None:
        """キャッシュからデータを取得する

        TTLを超過したエントリは削除してNoneを返す。

        Args:
            key: キャッシュキー

        Returns:
            キャッシュされたDataFrame。キャッシュミスの場合はNone。
        """
        entry = self._cache.get(key)
        if entry is None:
            return None

        elapsed = time.monotonic() - entry.created_at
        if elapsed > self._cache_ttl_sec:
            del self._cache[key]
            return None

        return entry.data

    def _set_cache(self, key: str, data: pd.DataFrame) -> None:
        """データをキャッシュに保存する

        Args:
            key: キャッシュキー
            data: 保存するDataFrame
        """
        self._cache[key] = _CacheEntry(data=data.copy(), created_at=time.monotonic())

    def invalidate_cache(self, key: str | None = None) -> None:
        """キャッシュを無効化する

        Args:
            key: 無効化するキー。Noneの場合は全キャッシュを削除する。
        """
        if key is None:
            self._cache.clear()
        else:
            self._cache.pop(key, None)

    def cache_ohlcv(self, symbol: str, timeframe: str, df: pd.DataFrame) -> None:
        """OHLCVデータをキャッシュに保存する

        Args:
            symbol: 通貨ペア（例: "BTC/USDT"）
            timeframe: 時間足（例: "1h"）
            df: OHLCVデータを含むDataFrame
        """
        key = f"{symbol}:{timeframe}"
        self._set_cache(key, df)

    def get_cached_ohlcv(self, symbol: str, timeframe: str) -> pd.DataFrame | None:
        """キャッシュからOHLCVデータを取得する

        Args:
            symbol: 通貨ペア
            timeframe: 時間足

        Returns:
            キャッシュされたDataFrame。キャッシュミスの場合はNone。
        """
        key = f"{symbol}:{timeframe}"
        return self._get_cached(key)

    @staticmethod
    def calculate_ema(df: pd.DataFrame, period: int = 9) -> pd.Series:
        """指数移動平均（EMA）を計算する

        Args:
            df: OHLCVデータを含むDataFrame（closeカラム必須）
            period: EMAの期間

        Returns:
            EMA値のSeries
        """
        result = ta.ema(df["close"], length=period)
        return result

    @staticmethod
    def calculate_adx(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
        """ADX（Average Directional Index）を計算する

        Args:
            df: OHLCVデータを含むDataFrame（high, low, closeカラム必須）
            period: ADXの期間

        Returns:
            ADX, DI+, DI-を含むDataFrame
        """
        result = ta.adx(df["high"], df["low"], df["close"], length=period)
        return result

    @staticmethod
    def calculate_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
        """ATR（Average True Range）を計算する

        Args:
            df: OHLCVデータを含むDataFrame（high, low, closeカラム必須）
            period: ATRの期間

        Returns:
            ATR値のSeries
        """
        result = ta.atr(df["high"], df["low"], df["close"], length=period)
        return result

    @staticmethod
    def calculate_rsi(df: pd.DataFrame, period: int = 14) -> pd.Series:
        """RSI（Relative Strength Index）を計算する

        Args:
            df: OHLCVデータを含むDataFrame（closeカラム必須）
            period: RSIの期間

        Returns:
            RSI値のSeries
        """
        result = ta.rsi(df["close"], length=period)
        return result

    @staticmethod
    def calculate_macd(
        df: pd.DataFrame,
        fast: int = 12,
        slow: int = 26,
        signal: int = 9,
    ) -> pd.DataFrame:
        """MACD（Moving Average Convergence Divergence）を計算する

        Args:
            df: OHLCVデータを含むDataFrame（closeカラム必須）
            fast: 短期EMAの期間
            slow: 長期EMAの期間
            signal: シグナル線の期間

        Returns:
            MACD, Signal, Histogramを含むDataFrame
        """
        result = ta.macd(df["close"], fast=fast, slow=slow, signal=signal)
        return result

    @staticmethod
    def calculate_bollinger_bands(
        df: pd.DataFrame,
        period: int = 20,
        std_dev: float = 2.0,
    ) -> pd.DataFrame:
        """ボリンジャーバンドを計算する

        Args:
            df: OHLCVデータを含むDataFrame（closeカラム必須）
            period: 移動平均の期間
            std_dev: 標準偏差の倍数

        Returns:
            Lower, Mid, Upper, Bandwidth, %Bを含むDataFrame
        """
        result = ta.bbands(df["close"], length=period, std=std_dev)
        return result

    def get_signals(self, df: pd.DataFrame, strategy_type: str) -> dict[str, Any]:
        """戦略タイプに応じたテクニカル指標を一括計算し、シグナル辞書を返す

        Args:
            df: OHLCVデータを含むDataFrame
            strategy_type: 戦略タイプ（"trend_follow", "rsi_reversal"など）

        Returns:
            計算された各種指標値とシグナル情報を含む辞書
        """
        signals: dict[str, Any] = {"strategy_type": strategy_type}

        if strategy_type == "trend_follow":
            signals.update(self._trend_follow_signals(df))
        elif strategy_type == "rsi_reversal":
            signals.update(self._rsi_reversal_signals(df))
        else:
            logger.warning("未知の戦略タイプです", strategy_type=strategy_type)
            signals["error"] = f"未知の戦略タイプ: {strategy_type}"

        return signals

    def _trend_follow_signals(self, df: pd.DataFrame) -> dict[str, Any]:
        """トレンドフォロー戦略のシグナルを計算する

        EMAクロスオーバー + ADXフィルターに基づくシグナルを生成する。

        Args:
            df: OHLCVデータを含むDataFrame

        Returns:
            トレンドフォロー指標とシグナルを含む辞書
        """
        ema_fast = self.calculate_ema(df, period=9)
        ema_slow = self.calculate_ema(df, period=21)
        adx_df = self.calculate_adx(df, period=14)
        atr = self.calculate_atr(df, period=14)

        # 最新値を取得
        latest_ema_fast = ema_fast.iloc[-1] if len(ema_fast) > 0 else None
        latest_ema_slow = ema_slow.iloc[-1] if len(ema_slow) > 0 else None

        # ADXのカラム名を取得（pandas-taの命名規則に従う）
        adx_col = f"ADX_14"
        dmp_col = f"DMP_14"
        dmn_col = f"DMN_14"

        latest_adx = adx_df[adx_col].iloc[-1] if adx_col in adx_df.columns else None
        latest_dmp = adx_df[dmp_col].iloc[-1] if dmp_col in adx_df.columns else None
        latest_dmn = adx_df[dmn_col].iloc[-1] if dmn_col in adx_df.columns else None
        latest_atr = atr.iloc[-1] if len(atr) > 0 else None

        # 出来高フィルター: 直近の出来高が平均の1.5倍以上か
        avg_volume = df["volume"].rolling(20).mean().iloc[-1]
        latest_volume = df["volume"].iloc[-1]
        volume_sufficient = latest_volume > avg_volume * 1.5 if avg_volume else False

        # シグナル判定
        signal = "neutral"
        if latest_ema_fast is not None and latest_ema_slow is not None and latest_adx is not None:
            golden_cross = latest_ema_fast > latest_ema_slow
            dead_cross = latest_ema_fast < latest_ema_slow
            strong_trend = latest_adx > 25

            if golden_cross and strong_trend and volume_sufficient:
                signal = "buy"
            elif dead_cross:
                signal = "sell"

        return {
            "signal": signal,
            "ema_fast": latest_ema_fast,
            "ema_slow": latest_ema_slow,
            "adx": latest_adx,
            "di_plus": latest_dmp,
            "di_minus": latest_dmn,
            "atr": latest_atr,
            "volume_sufficient": volume_sufficient,
        }

    def _rsi_reversal_signals(self, df: pd.DataFrame) -> dict[str, Any]:
        """RSI逆張り戦略のシグナルを計算する

        RSIの買われすぎ/売られすぎに基づくシグナルを生成する。

        Args:
            df: OHLCVデータを含むDataFrame

        Returns:
            RSI逆張り指標とシグナルを含む辞書
        """
        rsi = self.calculate_rsi(df, period=14)
        bb = self.calculate_bollinger_bands(df, period=20, std_dev=2.0)
        atr = self.calculate_atr(df, period=14)

        latest_rsi = rsi.iloc[-1] if len(rsi) > 0 else None
        latest_atr = atr.iloc[-1] if len(atr) > 0 else None
        latest_close = df["close"].iloc[-1]

        # ボリンジャーバンドの位置を判定
        bbl_col = "BBL_20_2.0"
        bbu_col = "BBU_20_2.0"
        below_lower = False
        above_upper = False
        if bb is not None and bbl_col in bb.columns and bbu_col in bb.columns:
            below_lower = latest_close < bb[bbl_col].iloc[-1]
            above_upper = latest_close > bb[bbu_col].iloc[-1]

        # シグナル判定
        signal = "neutral"
        if latest_rsi is not None:
            if latest_rsi < 30 and below_lower:
                signal = "buy"
            elif latest_rsi > 70 and above_upper:
                signal = "sell"

        return {
            "signal": signal,
            "rsi": latest_rsi,
            "atr": latest_atr,
            "below_lower_band": below_lower,
            "above_upper_band": above_upper,
        }
