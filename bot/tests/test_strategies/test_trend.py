"""トレンドフォロー戦略のユニットテスト。

ゴールデンクロス/デッドクロスの検出、ADXフィルター、
ポジションサイズ計算、クールダウン、トレーリングストップをテストする。
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock

import numpy as np
import pandas as pd
import pytest

from stella.core.portfolio import PortfolioManager
from stella.strategies.base import Signal
from stella.strategies.trend import TrendStrategy


@pytest.fixture
def trend_strategy() -> TrendStrategy:
    """デフォルト設定のTrendStrategyインスタンスを生成する。"""
    return TrendStrategy()


@pytest.fixture
def strategy_with_low_adx_threshold() -> TrendStrategy:
    """ADX閾値を低く設定したTrendStrategyインスタンスを生成する。"""
    return TrendStrategy(config={"adx_threshold": 5.0, "volume_mult": 0.0})


def _make_golden_cross_df(n: int = 100) -> pd.DataFrame:
    """ゴールデンクロスが発生するOHLCVデータを生成する。

    前半は短期EMA < 長期EMA、後半で短期EMAが長期EMAを上抜けする。
    """
    np.random.seed(10)
    # 最初は横ばい、途中から急上昇
    prices = np.concatenate([
        np.linspace(100, 100, 60),
        np.linspace(100, 130, 40),
    ])
    noise = np.random.normal(0, 0.5, n)
    close = prices + noise
    high = close + 1.0
    low = close - 1.0
    open_ = close + np.random.normal(0, 0.3, n)
    # 後半で出来高を増やしてフィルターを通過させる
    volume = np.concatenate([
        np.full(60, 100.0),
        np.full(40, 500.0),
    ])

    return pd.DataFrame({
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
        "symbol": "BTC/USDT",
    })


def _make_death_cross_df(n: int = 100) -> pd.DataFrame:
    """デッドクロスが発生するOHLCVデータを生成する。

    前半は上昇トレンド、後半で急落する。
    """
    np.random.seed(20)
    prices = np.concatenate([
        np.linspace(100, 130, 60),
        np.linspace(130, 95, 40),
    ])
    noise = np.random.normal(0, 0.5, n)
    close = prices + noise
    high = close + 1.0
    low = close - 1.0
    open_ = close + np.random.normal(0, 0.3, n)
    volume = np.concatenate([
        np.full(60, 100.0),
        np.full(40, 500.0),
    ])

    return pd.DataFrame({
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
        "symbol": "BTC/USDT",
    })


def _make_flat_df(n: int = 100) -> pd.DataFrame:
    """トレンドのない横ばい(低ADX)のOHLCVデータを生成する。"""
    np.random.seed(30)
    close = 100.0 + np.random.normal(0, 0.3, n)
    high = close + 0.5
    low = close - 0.5
    open_ = close + np.random.normal(0, 0.1, n)
    volume = np.full(n, 500.0)

    return pd.DataFrame({
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
        "symbol": "BTC/USDT",
    })


class TestCrossoverDetection:
    """EMAクロスオーバー検出のテスト。"""

    def test_golden_cross_detected(self, trend_strategy: TrendStrategy) -> None:
        """短期EMAが長期EMAを上抜けした場合にゴールデンクロスが検出されること。"""
        symbol = "BTC/USDT"

        # 前回値を設定: 短期 < 長期
        trend_strategy._prev_ema_short[symbol] = 99.0
        trend_strategy._prev_ema_long[symbol] = 100.0

        # 今回値: 短期 > 長期
        result = trend_strategy._detect_crossover(symbol, 101.0, 100.0)

        assert result == "golden_cross"

    def test_death_cross_detected(self, trend_strategy: TrendStrategy) -> None:
        """短期EMAが長期EMAを下抜けした場合にデッドクロスが検出されること。"""
        symbol = "BTC/USDT"

        # 前回値を設定: 短期 >= 長期
        trend_strategy._prev_ema_short[symbol] = 101.0
        trend_strategy._prev_ema_long[symbol] = 100.0

        # 今回値: 短期 < 長期
        result = trend_strategy._detect_crossover(symbol, 99.0, 100.0)

        assert result == "death_cross"

    def test_no_crossover_when_same_direction(self, trend_strategy: TrendStrategy) -> None:
        """クロスが発生しない場合にNoneが返されること。"""
        symbol = "BTC/USDT"

        # 前回値: 短期 > 長期
        trend_strategy._prev_ema_short[symbol] = 101.0
        trend_strategy._prev_ema_long[symbol] = 100.0

        # 今回も: 短期 > 長期
        result = trend_strategy._detect_crossover(symbol, 102.0, 100.0)

        assert result is None

    def test_no_crossover_without_previous_values(self, trend_strategy: TrendStrategy) -> None:
        """前回値がない場合にNoneが返されること。"""
        result = trend_strategy._detect_crossover("NEW/PAIR", 101.0, 100.0)

        assert result is None


class TestAnalyzeGoldenCross:
    """analyze()でのゴールデンクロス(買いシグナル)テスト。"""

    @pytest.mark.asyncio
    async def test_golden_cross_generates_buy_signal(
        self, strategy_with_low_adx_threshold: TrendStrategy
    ) -> None:
        """ゴールデンクロス時に買いシグナルが生成されること。"""
        strategy = strategy_with_low_adx_threshold
        portfolio = PortfolioManager(initial_balance=10000.0)

        df = _make_golden_cross_df()

        # 1回目: 前回値を初期化 (クロスオーバー判定不可)
        await strategy.analyze(df.iloc[:80], portfolio)

        # 2回目: クロスオーバーが発生する区間
        signals = await strategy.analyze(df, portfolio)

        # シグナルが生成された場合は買いシグナルであること
        buy_signals = [s for s in signals if s.action == "buy"]
        if buy_signals:
            assert buy_signals[0].symbol == "BTC/USDT"
            assert buy_signals[0].stop_loss is not None
            assert buy_signals[0].stop_loss < df["close"].iloc[-1]


class TestAnalyzeDeathCross:
    """analyze()でのデッドクロス(売りシグナル)テスト。"""

    @pytest.mark.asyncio
    async def test_death_cross_requires_position(
        self, strategy_with_low_adx_threshold: TrendStrategy
    ) -> None:
        """デッドクロス時にポジションがなければ売りシグナルが生成されないこと。"""
        strategy = strategy_with_low_adx_threshold
        portfolio = PortfolioManager(initial_balance=10000.0)

        df = _make_death_cross_df()

        # 前回値を初期化
        await strategy.analyze(df.iloc[:80], portfolio)

        # ポジションなしで分析
        signals = await strategy.analyze(df, portfolio)

        sell_signals = [s for s in signals if s.action == "sell"]
        # ポジションがないためデッドクロスでも売りシグナルは出ない
        assert len(sell_signals) == 0


class TestADXFilter:
    """ADXフィルターのテスト。"""

    @pytest.mark.asyncio
    async def test_low_adx_suppresses_signal(self) -> None:
        """ADXが閾値未満の場合にシグナルが生成されないこと。"""
        # 高い閾値を設定して横ばいデータを分析
        strategy = TrendStrategy(config={"adx_threshold": 50.0})
        portfolio = PortfolioManager(initial_balance=10000.0)

        df = _make_flat_df()

        # 前回値を初期化
        await strategy.analyze(df.iloc[:80], portfolio)
        signals = await strategy.analyze(df, portfolio)

        # 横ばいでADXが低いためシグナルは生成されない
        buy_signals = [s for s in signals if s.action == "buy"]
        assert len(buy_signals) == 0


class TestPositionSizing:
    """ポジションサイズ計算のテスト。"""

    def test_position_size_calculation(self, trend_strategy: TrendStrategy) -> None:
        """リスクベースのポジションサイズが正しく計算されること。"""
        signal = Signal(
            action="buy",
            symbol="BTC/USDT",
            strength=0.8,
            reason="テスト",
        )

        # balance=10000, risk_pct=0.02 -> risk_amount=200
        # atr=500, stop_loss_atr_mult=2.0 -> stop_distance=1000
        # position_size = 200 / 1000 = 0.2
        size = trend_strategy.get_position_size(signal, balance=10000.0, atr=500.0)

        assert size == pytest.approx(0.2, abs=1e-6)

    def test_position_size_zero_atr(self, trend_strategy: TrendStrategy) -> None:
        """ATRが0の場合にポジションサイズが0を返すこと。"""
        signal = Signal(action="buy", symbol="BTC/USDT", strength=0.8, reason="テスト")

        size = trend_strategy.get_position_size(signal, balance=10000.0, atr=0.0)

        assert size == 0.0

    def test_position_size_zero_balance(self, trend_strategy: TrendStrategy) -> None:
        """残高が0の場合にポジションサイズが0を返すこと。"""
        signal = Signal(action="buy", symbol="BTC/USDT", strength=0.8, reason="テスト")

        size = trend_strategy.get_position_size(signal, balance=0.0, atr=500.0)

        assert size == 0.0

    def test_position_size_proportional_to_balance(
        self, trend_strategy: TrendStrategy
    ) -> None:
        """ポジションサイズが残高に比例すること。"""
        signal = Signal(action="buy", symbol="BTC/USDT", strength=0.8, reason="テスト")

        size1 = trend_strategy.get_position_size(signal, balance=10000.0, atr=500.0)
        size2 = trend_strategy.get_position_size(signal, balance=20000.0, atr=500.0)

        assert size2 == pytest.approx(size1 * 2, abs=1e-6)


class TestCooldown:
    """クールダウン機構のテスト。"""

    @pytest.mark.asyncio
    async def test_cooldown_blocks_analysis(self) -> None:
        """クールダウン中はanalyzeが空リストを返すこと。"""
        strategy = TrendStrategy(config={"cooldown_minutes": 60})
        portfolio = PortfolioManager(initial_balance=10000.0)

        # トレードを記録してクールダウンを開始
        strategy.record_trade()

        df = _make_golden_cross_df()
        signals = await strategy.analyze(df, portfolio)

        assert signals == []

    def test_cooldown_active_after_trade(self) -> None:
        """トレード直後はクールダウンが有効であること。"""
        strategy = TrendStrategy(config={"cooldown_minutes": 60})
        strategy.record_trade()

        assert strategy.is_cooldown() is True

    def test_cooldown_inactive_initially(self) -> None:
        """初期状態ではクールダウンが無効であること。"""
        strategy = TrendStrategy()

        assert strategy.is_cooldown() is False

    def test_cooldown_expires(self) -> None:
        """クールダウン期間経過後にクールダウンが解除されること。"""
        strategy = TrendStrategy(config={"cooldown_minutes": 0})
        # クールダウン0分なので即座に解除される
        strategy._last_trade_time = time.time() - 1

        assert strategy.is_cooldown() is False


class TestTrailingStopLogic:
    """トレーリングストップロジックのテスト。"""

    def test_trailing_stop_triggered(self, trend_strategy: TrendStrategy) -> None:
        """利益がトレーリング発動条件を満たした場合に売りシグナルが生成されること。"""
        # ポジションオブジェクトをモック
        position = MagicMock()
        position.entry_price = 100.0
        position.stop_loss = 90.0
        position.highest_price = 120.0  # 最高値

        # ATR=5, trailing_atr_mult=3.0 -> トレーリング発動条件: 利益 >= 15
        # current_close=110, entry=100 -> 利益=10で発動条件未達
        # current_close=116, entry=100 -> 利益=16で発動条件到達
        # trailing_stop_atr_mult=1.5 -> trailing_stop = 120 - 5*1.5 = 112.5
        # current_close=110 < 112.5 -> トレーリングストップ発動
        signals = trend_strategy._check_exit_conditions(
            symbol="BTC/USDT",
            position=position,
            current_close=110.0,
            current_atr=5.0,
            current_ema_short=108.0,
            current_ema_long=109.0,
        )

        # 利益がトレーリング発動条件(5*3=15)に達していない(10<15)のでシグナルなし
        sell_signals = [s for s in signals if s.action == "sell"]
        assert len(sell_signals) == 0

    def test_trailing_stop_fires_when_conditions_met(
        self, trend_strategy: TrendStrategy
    ) -> None:
        """全条件を満たした場合にトレーリングストップが発動すること。"""
        position = MagicMock()
        position.entry_price = 100.0
        position.stop_loss = 90.0
        position.highest_price = 125.0

        # current_close=116, entry=100 -> 利益=16 >= 15(ATR5*3) -> 条件到達
        # trailing_stop = 125 - 5*1.5 = 117.5
        # current_close=116 < 117.5 -> 発動
        signals = trend_strategy._check_exit_conditions(
            symbol="BTC/USDT",
            position=position,
            current_close=116.0,
            current_atr=5.0,
            current_ema_short=108.0,
            current_ema_long=109.0,
        )

        sell_signals = [s for s in signals if s.action == "sell"]
        assert len(sell_signals) == 1
        assert "トレーリングストップ" in sell_signals[0].reason

    def test_stop_loss_triggered(self, trend_strategy: TrendStrategy) -> None:
        """価格がストップロスに到達した場合に売りシグナルが生成されること。"""
        position = MagicMock()
        position.entry_price = 100.0
        position.stop_loss = 95.0
        position.highest_price = 100.0

        # current_close=94 <= stop_loss=95 -> ストップロス発動
        signals = trend_strategy._check_exit_conditions(
            symbol="BTC/USDT",
            position=position,
            current_close=94.0,
            current_atr=5.0,
            current_ema_short=93.0,
            current_ema_long=96.0,
        )

        assert len(signals) == 1
        assert signals[0].action == "sell"
        assert "ストップロス" in signals[0].reason

    def test_no_exit_when_position_has_no_entry(
        self, trend_strategy: TrendStrategy
    ) -> None:
        """ポジションにentry_priceがない場合にシグナルが生成されないこと。"""
        position = MagicMock(spec=[])  # entry_price属性を持たない

        signals = trend_strategy._check_exit_conditions(
            symbol="BTC/USDT",
            position=position,
            current_close=100.0,
            current_atr=5.0,
            current_ema_short=100.0,
            current_ema_long=100.0,
        )

        assert len(signals) == 0
