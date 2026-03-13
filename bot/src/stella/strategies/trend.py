"""トレンドフォロー戦略モジュール。

EMAクロスオーバーとADXフィルターを組み合わせたトレンドフォロー戦略を実装する。
ゴールデンクロス/デッドクロスの検出、ADXによるトレンド強度フィルタリング、
出来高フィルター、ATRベースのストップロス/トレーリングストップを提供する。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np
import pandas as pd
import structlog

from stella.strategies.base import BaseStrategy, Signal

if TYPE_CHECKING:
    from stella.core.portfolio import PortfolioManager

logger = structlog.get_logger(__name__)

# デフォルトパラメータ
DEFAULT_EMA_SHORT = 9
DEFAULT_EMA_LONG = 21
DEFAULT_ADX_PERIOD = 14
DEFAULT_ADX_THRESHOLD = 25.0
DEFAULT_ATR_PERIOD = 14
DEFAULT_STOP_LOSS_ATR_MULT = 2.0
DEFAULT_TRAILING_ATR_MULT = 3.0
DEFAULT_TRAILING_STOP_ATR_MULT = 1.5
DEFAULT_COOLDOWN_MINUTES = 60
DEFAULT_RISK_PCT = 0.02
DEFAULT_VOLUME_MULT = 1.5


class TrendStrategy(BaseStrategy):
    """EMAクロス + ADXフィルターによるトレンドフォロー戦略。

    買いシグナル:
        - EMA(短期) > EMA(長期) (ゴールデンクロス)
        - ADX > 閾値 (十分なトレンド強度)
        - 出来高 > 平均出来高 * 1.5
        - ATRベースのストップロス設定

    売りシグナル:
        - EMA(短期) < EMA(長期) (デッドクロス)
        - トレーリングストップ発動
        - 固定ストップロス発動

    Attributes:
        ema_short: 短期EMA期間
        ema_long: 長期EMA期間
        adx_period: ADX計算期間
        adx_threshold: ADXフィルター閾値
        atr_period: ATR計算期間
        stop_loss_atr_mult: ストップロスのATR倍率
        trailing_atr_mult: トレーリングストップ発動のATR倍率
        trailing_stop_atr_mult: トレーリングストップ追従のATR倍率
        risk_pct: 1トレードあたりのリスク割合
        volume_mult: 出来高フィルター倍率
        _prev_ema_short: 前回の短期EMA値 (シンボル別)
        _prev_ema_long: 前回の長期EMA値 (シンボル別)
    """

    def __init__(self, config: dict | None = None) -> None:
        """トレンドフォロー戦略を初期化する。

        Args:
            config: 戦略設定パラメータ。Noneの場合はデフォルト値を使用する。
        """
        config = config or {}
        super().__init__(name="trend_follow", config=config)

        self.ema_short: int = config.get("ema_short", DEFAULT_EMA_SHORT)
        self.ema_long: int = config.get("ema_long", DEFAULT_EMA_LONG)
        self.adx_period: int = config.get("adx_period", DEFAULT_ADX_PERIOD)
        self.adx_threshold: float = config.get("adx_threshold", DEFAULT_ADX_THRESHOLD)
        self.atr_period: int = config.get("atr_period", DEFAULT_ATR_PERIOD)
        self.stop_loss_atr_mult: float = config.get(
            "stop_loss_atr_mult", DEFAULT_STOP_LOSS_ATR_MULT
        )
        self.trailing_atr_mult: float = config.get(
            "trailing_atr_mult", DEFAULT_TRAILING_ATR_MULT
        )
        self.trailing_stop_atr_mult: float = config.get(
            "trailing_stop_atr_mult", DEFAULT_TRAILING_STOP_ATR_MULT
        )
        self.risk_pct: float = config.get("risk_pct", DEFAULT_RISK_PCT)
        self.volume_mult: float = config.get("volume_mult", DEFAULT_VOLUME_MULT)

        # クロスオーバー検出用の前回EMA値をシンボル別に保持
        self._prev_ema_short: dict[str, float] = {}
        self._prev_ema_long: dict[str, float] = {}

        logger.info(
            "トレンドフォロー戦略を初期化しました",
            ema_short=self.ema_short,
            ema_long=self.ema_long,
            adx_threshold=self.adx_threshold,
        )

    def _calculate_ema(self, series: pd.Series, period: int) -> pd.Series:
        """指数移動平均(EMA)を計算する。

        Args:
            series: 価格系列データ
            period: EMA期間

        Returns:
            EMA値のSeries
        """
        return series.ewm(span=period, adjust=False).mean()

    def _calculate_adx(self, df: pd.DataFrame) -> pd.Series:
        """ADX(Average Directional Index)を計算する。

        Args:
            df: OHLCVデータを含むDataFrame

        Returns:
            ADX値のSeries
        """
        high = df["high"]
        low = df["low"]
        close = df["close"]
        period = self.adx_period

        # True Range
        tr1 = high - low
        tr2 = (high - close.shift(1)).abs()
        tr3 = (low - close.shift(1)).abs()
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

        # +DM / -DM
        up_move = high - high.shift(1)
        down_move = low.shift(1) - low
        plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
        minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)

        # Wilder's smoothing (EMA相当)
        atr = pd.Series(tr, index=df.index).ewm(span=period, adjust=False).mean()
        plus_di = (
            100
            * pd.Series(plus_dm, index=df.index).ewm(span=period, adjust=False).mean()
            / atr
        )
        minus_di = (
            100
            * pd.Series(minus_dm, index=df.index).ewm(span=period, adjust=False).mean()
            / atr
        )

        # ADX
        dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
        adx = dx.ewm(span=period, adjust=False).mean()

        return adx

    def _calculate_atr(self, df: pd.DataFrame) -> pd.Series:
        """ATR(Average True Range)を計算する。

        Args:
            df: OHLCVデータを含むDataFrame

        Returns:
            ATR値のSeries
        """
        high = df["high"]
        low = df["low"]
        close = df["close"]

        tr1 = high - low
        tr2 = (high - close.shift(1)).abs()
        tr3 = (low - close.shift(1)).abs()
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

        return tr.ewm(span=self.atr_period, adjust=False).mean()

    def _detect_crossover(
        self, symbol: str, current_ema_short: float, current_ema_long: float
    ) -> str | None:
        """EMAクロスオーバーを検出する。

        前回のEMA値と比較してゴールデンクロス/デッドクロスを判定する。

        Args:
            symbol: シンボル名
            current_ema_short: 現在の短期EMA値
            current_ema_long: 現在の長期EMA値

        Returns:
            "golden_cross", "death_cross", またはNone
        """
        prev_short = self._prev_ema_short.get(symbol)
        prev_long = self._prev_ema_long.get(symbol)

        # 前回値を更新
        self._prev_ema_short[symbol] = current_ema_short
        self._prev_ema_long[symbol] = current_ema_long

        # 前回値がない場合はクロスオーバー判定不可
        if prev_short is None or prev_long is None:
            return None

        # ゴールデンクロス: 前回は短期 <= 長期で、今回は短期 > 長期
        if prev_short <= prev_long and current_ema_short > current_ema_long:
            logger.info(
                "ゴールデンクロスを検出しました",
                symbol=symbol,
                ema_short=round(current_ema_short, 4),
                ema_long=round(current_ema_long, 4),
            )
            return "golden_cross"

        # デッドクロス: 前回は短期 >= 長期で、今回は短期 < 長期
        if prev_short >= prev_long and current_ema_short < current_ema_long:
            logger.info(
                "デッドクロスを検出しました",
                symbol=symbol,
                ema_short=round(current_ema_short, 4),
                ema_long=round(current_ema_long, 4),
            )
            return "death_cross"

        return None

    async def analyze(
        self, df: pd.DataFrame, portfolio: PortfolioManager
    ) -> list[Signal]:
        """市場データを分析してトレンドフォローシグナルを生成する。

        処理フロー:
        1. EMA(短期/長期)を計算
        2. クロスオーバーを検出 (ゴールデンクロス=買い、デッドクロス=売り)
        3. ADXフィルター (閾値以上のトレンド強度)
        4. 出来高フィルター (平均の1.5倍以上)
        5. ATRベースのストップロスを計算
        6. 既存ポジションのトレーリングストップ/ストップロスを確認

        Args:
            df: OHLCVデータを含むDataFrame
                (columns: open, high, low, close, volume, symbol)
            portfolio: ポートフォリオマネージャー

        Returns:
            生成されたシグナルのリスト
        """
        if not self.is_active:
            return []

        if self.is_cooldown():
            return []

        # 必要な最低行数チェック
        min_rows = max(self.ema_long, self.adx_period, self.atr_period) + 5
        if len(df) < min_rows:
            logger.warning(
                "データ行数が不足しています",
                required=min_rows,
                actual=len(df),
            )
            return []

        signals: list[Signal] = []

        # シンボルを取得 (DataFrameにsymbol列がある場合はそれを使用)
        symbol = df["symbol"].iloc[0] if "symbol" in df.columns else "UNKNOWN"

        # テクニカル指標を計算
        ema_short = self._calculate_ema(df["close"], self.ema_short)
        ema_long = self._calculate_ema(df["close"], self.ema_long)
        adx = self._calculate_adx(df)
        atr = self._calculate_atr(df)

        # 最新値を取得
        current_close = df["close"].iloc[-1]
        current_ema_short = ema_short.iloc[-1]
        current_ema_long = ema_long.iloc[-1]
        current_adx = adx.iloc[-1]
        current_atr = atr.iloc[-1]
        current_volume = df["volume"].iloc[-1]
        avg_volume = df["volume"].rolling(window=20).mean().iloc[-1]

        logger.debug(
            "テクニカル指標を計算しました",
            symbol=symbol,
            ema_short=round(current_ema_short, 4),
            ema_long=round(current_ema_long, 4),
            adx=round(current_adx, 2),
            atr=round(current_atr, 4),
        )

        # 既存ポジションの確認
        position = portfolio.get_position(symbol) if hasattr(portfolio, "get_position") else None

        # 既存ポジションがある場合: トレーリングストップ/ストップロス判定
        if position is not None:
            signals.extend(
                self._check_exit_conditions(
                    symbol=symbol,
                    position=position,
                    current_close=current_close,
                    current_atr=current_atr,
                    current_ema_short=current_ema_short,
                    current_ema_long=current_ema_long,
                )
            )

        # クロスオーバー検出
        crossover = self._detect_crossover(symbol, current_ema_short, current_ema_long)

        if crossover is None:
            return signals

        # ADXフィルター
        if current_adx < self.adx_threshold:
            logger.debug(
                "ADXが閾値未満のためシグナルをスキップします",
                symbol=symbol,
                adx=round(current_adx, 2),
                threshold=self.adx_threshold,
            )
            return signals

        # 出来高フィルター
        if pd.isna(avg_volume) or avg_volume == 0:
            logger.debug("平均出来高が計算できません", symbol=symbol)
            return signals

        if current_volume < avg_volume * self.volume_mult:
            logger.debug(
                "出来高が不足しているためシグナルをスキップします",
                symbol=symbol,
                current_volume=current_volume,
                threshold=round(avg_volume * self.volume_mult, 2),
            )
            return signals

        # シグナル強度を計算 (ADXとEMA乖離率から算出)
        ema_spread = abs(current_ema_short - current_ema_long) / current_ema_long
        strength = min(1.0, (current_adx / 50.0) * 0.6 + min(ema_spread * 100, 1.0) * 0.4)

        if crossover == "golden_cross":
            # 買いシグナル
            stop_loss = current_close - current_atr * self.stop_loss_atr_mult
            signals.append(
                Signal(
                    action="buy",
                    symbol=symbol,
                    strength=round(strength, 3),
                    reason=(
                        f"ゴールデンクロス検出: "
                        f"EMA({self.ema_short})={current_ema_short:.4f} > "
                        f"EMA({self.ema_long})={current_ema_long:.4f}, "
                        f"ADX={current_adx:.1f}"
                    ),
                    stop_loss=round(stop_loss, 4),
                    take_profit=None,
                )
            )

        elif crossover == "death_cross":
            # 売りシグナル (ポジションがある場合のみ)
            if position is not None:
                signals.append(
                    Signal(
                        action="sell",
                        symbol=symbol,
                        strength=round(strength, 3),
                        reason=(
                            f"デッドクロス検出: "
                            f"EMA({self.ema_short})={current_ema_short:.4f} < "
                            f"EMA({self.ema_long})={current_ema_long:.4f}, "
                            f"ADX={current_adx:.1f}"
                        ),
                        stop_loss=None,
                        take_profit=None,
                    )
                )

        return signals

    def _check_exit_conditions(
        self,
        symbol: str,
        position: object,
        current_close: float,
        current_atr: float,
        current_ema_short: float,
        current_ema_long: float,
    ) -> list[Signal]:
        """既存ポジションの決済条件を確認する。

        トレーリングストップとストップロスを確認し、
        条件を満たした場合は売りシグナルを生成する。

        Args:
            symbol: シンボル名
            position: ポジションオブジェクト
            current_close: 現在の終値
            current_atr: 現在のATR値
            current_ema_short: 現在の短期EMA
            current_ema_long: 現在の長期EMA

        Returns:
            決済シグナルのリスト (条件を満たさない場合は空リスト)
        """
        signals: list[Signal] = []
        entry_price = getattr(position, "entry_price", None)

        if entry_price is None:
            return signals

        # ストップロスチェック
        stop_loss = getattr(position, "stop_loss", None)
        if stop_loss is not None and current_close <= stop_loss:
            logger.info(
                "ストップロスに到達しました",
                symbol=symbol,
                close=current_close,
                stop_loss=stop_loss,
            )
            signals.append(
                Signal(
                    action="sell",
                    symbol=symbol,
                    strength=1.0,
                    reason=f"ストップロス発動: 現在価格{current_close:.4f} <= SL{stop_loss:.4f}",
                    stop_loss=None,
                    take_profit=None,
                )
            )
            return signals

        # トレーリングストップチェック
        # 利益がATR * trailing_atr_multを超えた場合にトレーリングストップを設定
        profit_distance = current_close - entry_price
        trailing_trigger = current_atr * self.trailing_atr_mult

        if profit_distance >= trailing_trigger:
            trailing_stop = current_close - current_atr * self.trailing_stop_atr_mult
            # ポジションの最高値を使用してトレーリングストップを更新
            highest = getattr(position, "highest_price", current_close)
            if highest > current_close:
                trailing_stop_from_high = highest - current_atr * self.trailing_stop_atr_mult
                if current_close <= trailing_stop_from_high:
                    logger.info(
                        "トレーリングストップに到達しました",
                        symbol=symbol,
                        close=current_close,
                        trailing_stop=trailing_stop_from_high,
                    )
                    signals.append(
                        Signal(
                            action="sell",
                            symbol=symbol,
                            strength=0.9,
                            reason=(
                                f"トレーリングストップ発動: "
                                f"現在価格{current_close:.4f}, "
                                f"最高値{highest:.4f}, "
                                f"TS{trailing_stop_from_high:.4f}"
                            ),
                            stop_loss=None,
                            take_profit=None,
                        )
                    )

        return signals

    def get_position_size(self, signal: Signal, balance: float, atr: float) -> float:
        """リスクベースのポジションサイズを計算する。

        計算式: (残高 * リスク割合) / (ATR * ストップロスATR倍率)

        Args:
            signal: トレーディングシグナル
            balance: 現在の利用可能残高
            atr: 現在のATR値

        Returns:
            ポジションサイズ (数量)。計算不能な場合は0.0を返す。
        """
        if atr <= 0 or balance <= 0:
            logger.warning(
                "ポジションサイズ計算に必要な値が不正です",
                balance=balance,
                atr=atr,
            )
            return 0.0

        risk_amount = balance * self.risk_pct
        stop_distance = atr * self.stop_loss_atr_mult

        if stop_distance <= 0:
            return 0.0

        position_size = risk_amount / stop_distance

        logger.info(
            "ポジションサイズを計算しました",
            symbol=signal.symbol,
            balance=balance,
            risk_amount=round(risk_amount, 2),
            stop_distance=round(stop_distance, 4),
            position_size=round(position_size, 6),
        )

        return round(position_size, 8)
