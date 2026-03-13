"""安全機構のユニットテスト。

日次損失制限、最大ドローダウン、ボラティリティチェック、
1トレードリスクチェック、キルスイッチ、一時停止/再開をテストする。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from stella.core.portfolio import PortfolioManager
from stella.core.safety import SafetyConfig, SafetyManager


@pytest.fixture
def safety_setup() -> tuple[SafetyManager, PortfolioManager]:
    """テスト用のSafetyManagerとPortfolioManagerのペアを生成する。"""
    portfolio = PortfolioManager(initial_balance=10000.0, max_positions=3)
    config = SafetyConfig(
        daily_loss_limit=0.05,
        max_drawdown=0.20,
        volatility_multiplier=2.0,
        max_trade_risk=0.02,
        volatility_pause_duration=300,
        max_consecutive_losses=5,
    )
    safety = SafetyManager(config=config, portfolio=portfolio)
    return safety, portfolio


class TestDailyLossLimit:
    """日次損失制限のテスト。"""

    def test_within_daily_loss_limit(self, safety_setup: tuple) -> None:
        """日次損失が上限以内の場合にTrueを返すこと。"""
        safety, _ = safety_setup

        # 残高10000の5%=500が上限。損失300は上限以内。
        assert safety.check_daily_loss(daily_pnl=-300.0, total_balance=10000.0) is True

    def test_exceeds_daily_loss_limit(self, safety_setup: tuple) -> None:
        """日次損失が上限を超えた場合にFalseを返すこと。"""
        safety, _ = safety_setup

        # 残高10000の5%=500が上限。損失600は上限超過。
        assert safety.check_daily_loss(daily_pnl=-600.0, total_balance=10000.0) is False

    def test_exactly_at_daily_loss_limit(self, safety_setup: tuple) -> None:
        """日次損失がちょうど上限の場合にTrueを返すこと(境界値)。"""
        safety, _ = safety_setup

        # ちょうど上限(-500)の場合はセーフ
        assert safety.check_daily_loss(daily_pnl=-500.0, total_balance=10000.0) is True

    def test_zero_balance_returns_false(self, safety_setup: tuple) -> None:
        """残高が0の場合にFalseを返すこと。"""
        safety, _ = safety_setup

        assert safety.check_daily_loss(daily_pnl=0.0, total_balance=0.0) is False

    def test_can_trade_blocked_by_daily_loss(self, safety_setup: tuple) -> None:
        """日次損失上限超過時にcan_tradeがFalseを返すこと。"""
        safety, portfolio = safety_setup

        # ポートフォリオの残高を操作して大きな損失を発生させる
        pos = portfolio.open_position("BTC/USDT", "buy", 40000.0, 0.5, "trend_follow")
        portfolio.close_position(pos.position_id, 38800.0)
        # PnL = (38800 - 40000) * 0.5 = -600 (10000の6% > 5%上限)

        can, reason = safety.can_trade()
        assert can is False
        assert "日次損失上限" in reason


class TestMaxDrawdown:
    """最大ドローダウンのテスト。"""

    def test_within_drawdown_limit(self, safety_setup: tuple) -> None:
        """ドローダウンが上限以内の場合にTrueを返すこと。"""
        safety, _ = safety_setup

        # ピーク10000から9000へ(10% < 20%上限)
        assert safety.check_drawdown(current_balance=9000.0, peak_balance=10000.0) is True

    def test_exceeds_drawdown_limit(self, safety_setup: tuple) -> None:
        """ドローダウンが上限を超えた場合にFalseを返すこと。"""
        safety, _ = safety_setup

        # ピーク10000から7500へ(25% > 20%上限)
        assert safety.check_drawdown(current_balance=7500.0, peak_balance=10000.0) is False

    def test_zero_peak_balance_returns_false(self, safety_setup: tuple) -> None:
        """ピーク残高が0の場合にFalseを返すこと。"""
        safety, _ = safety_setup

        assert safety.check_drawdown(current_balance=1000.0, peak_balance=0.0) is False

    def test_can_trade_blocked_by_drawdown(self) -> None:
        """ドローダウン超過時にcan_tradeがFalseを返すこと。"""
        portfolio = PortfolioManager(initial_balance=10000.0)
        # ピーク残高を高めに設定
        portfolio._peak_balance = 15000.0
        # 現在残高を低く設定 (ドローダウン = (15000 - 10000) / 15000 = 33%)
        config = SafetyConfig(max_drawdown=0.20)
        safety = SafetyManager(config=config, portfolio=portfolio)

        can, reason = safety.can_trade()
        assert can is False
        assert "ドローダウン" in reason


class TestVolatilityCheck:
    """ボラティリティチェックのテスト。"""

    def test_normal_volatility_passes(self, safety_setup: tuple) -> None:
        """通常のボラティリティ(倍率2.0未満)でTrueを返すこと。"""
        safety, _ = safety_setup

        # ATRが平均の1.5倍 < 閾値2.0倍
        assert safety.check_volatility(atr=150.0, avg_atr=100.0) is True

    def test_high_volatility_fails(self, safety_setup: tuple) -> None:
        """高ボラティリティ(倍率2.0以上)でFalseを返し一時停止すること。"""
        safety, _ = safety_setup

        # ATRが平均の2.5倍 > 閾値2.0倍
        result = safety.check_volatility(atr=250.0, avg_atr=100.0)

        assert result is False
        assert safety._is_paused is True
        assert "ボラティリティ" in safety._pause_reason

    def test_zero_avg_atr_returns_false(self, safety_setup: tuple) -> None:
        """平均ATRが0の場合にFalseを返すこと。"""
        safety, _ = safety_setup

        assert safety.check_volatility(atr=100.0, avg_atr=0.0) is False

    def test_volatility_exactly_at_threshold(self, safety_setup: tuple) -> None:
        """ATRがちょうど閾値倍率の場合にFalseを返すこと(境界値)。"""
        safety, _ = safety_setup

        # ちょうど2.0倍: ratio < multiplierではないのでFalse
        assert safety.check_volatility(atr=200.0, avg_atr=100.0) is False


class TestPerTradeRiskCheck:
    """1トレードあたりのリスクチェックのテスト。"""

    def test_risk_within_limit(self, safety_setup: tuple) -> None:
        """リスク額が上限以内の場合にTrueを返すこと。"""
        safety, _ = safety_setup

        # 残高10000の2%=200が上限。リスク150は上限以内。
        assert safety.check_trade_risk(risk_amount=150.0, total_balance=10000.0) is True

    def test_risk_exceeds_limit(self, safety_setup: tuple) -> None:
        """リスク額が上限を超えた場合にFalseを返すこと。"""
        safety, _ = safety_setup

        # 残高10000の2%=200が上限。リスク300は上限超過。
        assert safety.check_trade_risk(risk_amount=300.0, total_balance=10000.0) is False

    def test_risk_exactly_at_limit(self, safety_setup: tuple) -> None:
        """リスク額がちょうど上限の場合にTrueを返すこと(境界値)。"""
        safety, _ = safety_setup

        # ちょうど上限(200): risk_amount <= max_riskなのでTrue
        assert safety.check_trade_risk(risk_amount=200.0, total_balance=10000.0) is True

    def test_zero_balance_returns_false(self, safety_setup: tuple) -> None:
        """残高が0の場合にFalseを返すこと。"""
        safety, _ = safety_setup

        assert safety.check_trade_risk(risk_amount=10.0, total_balance=0.0) is False


class TestKillSwitch:
    """キルスイッチのテスト。"""

    @pytest.mark.asyncio
    async def test_kill_switch_closes_all_positions(
        self, safety_setup: tuple, mock_exchange: MagicMock
    ) -> None:
        """キルスイッチが全ポジションを決済すること。"""
        safety, portfolio = safety_setup

        # ポジションを2つ開設
        portfolio.open_position("BTC/USDT", "buy", 40000.0, 0.1, "trend_follow")
        portfolio.open_position("ETH/USDT", "buy", 3000.0, 1.0, "trend_follow")

        results = await safety.kill_switch(mock_exchange, portfolio)

        assert len(results) == 2
        assert all(r["success"] for r in results)
        assert len(portfolio.get_positions()) == 0

    @pytest.mark.asyncio
    async def test_kill_switch_activates_flag(
        self, safety_setup: tuple, mock_exchange: MagicMock
    ) -> None:
        """キルスイッチ発動後にフラグが設定されること。"""
        safety, portfolio = safety_setup

        await safety.kill_switch(mock_exchange, portfolio)

        assert safety._kill_switch_activated is True
        assert safety._is_paused is True

    @pytest.mark.asyncio
    async def test_kill_switch_blocks_trading(
        self, safety_setup: tuple, mock_exchange: MagicMock
    ) -> None:
        """キルスイッチ発動後にcan_tradeがFalseを返すこと。"""
        safety, portfolio = safety_setup

        await safety.kill_switch(mock_exchange, portfolio)

        can, reason = safety.can_trade()
        assert can is False
        assert "キルスイッチ" in reason

    @pytest.mark.asyncio
    async def test_kill_switch_handles_exchange_error(
        self, safety_setup: tuple
    ) -> None:
        """取引所エラー時にキルスイッチがエラー情報を記録すること。"""
        safety, portfolio = safety_setup
        portfolio.open_position("BTC/USDT", "buy", 40000.0, 0.1, "trend_follow")

        # 取引所がエラーを返す
        exchange = MagicMock()
        exchange.create_market_order = AsyncMock(side_effect=Exception("接続エラー"))

        results = await safety.kill_switch(exchange, portfolio)

        assert len(results) == 1
        assert results[0]["success"] is False
        assert "接続エラー" in results[0]["error"]


class TestPauseResume:
    """取引の一時停止と再開のテスト。"""

    def test_pause_sets_flag(self, safety_setup: tuple) -> None:
        """pauseが一時停止フラグと理由を設定すること。"""
        safety, _ = safety_setup

        safety.pause("メンテナンスのため")

        assert safety._is_paused is True
        assert safety._pause_reason == "メンテナンスのため"

    def test_paused_blocks_trading(self, safety_setup: tuple) -> None:
        """一時停止中はcan_tradeがFalseを返すこと。"""
        safety, _ = safety_setup

        safety.pause("テスト停止")

        can, reason = safety.can_trade()
        assert can is False
        assert "一時停止中" in reason

    def test_resume_clears_pause(self, safety_setup: tuple) -> None:
        """resumeが一時停止状態を解除すること。"""
        safety, _ = safety_setup

        safety.pause("テスト停止")
        safety.resume()

        assert safety._is_paused is False
        assert safety._pause_reason == ""

    def test_resume_clears_kill_switch(self, safety_setup: tuple) -> None:
        """resumeがキルスイッチフラグも解除すること。"""
        safety, _ = safety_setup

        safety._kill_switch_activated = True
        safety._is_paused = True
        safety.resume()

        assert safety._kill_switch_activated is False
        assert safety._is_paused is False


class TestCanTradeIntegration:
    """can_tradeメソッドの統合テスト。"""

    def test_normal_state_allows_trading(self, safety_setup: tuple) -> None:
        """正常状態で取引可能であること。"""
        safety, _ = safety_setup

        can, reason = safety.can_trade()
        assert can is True
        assert reason == "取引可能"

    def test_consecutive_losses_blocks_trading(self, safety_setup: tuple) -> None:
        """連続損失回数が上限に達した場合に取引が停止されること。"""
        safety, _ = safety_setup

        # 5回連続で損失を記録
        for _ in range(5):
            safety.record_trade(pnl=-50.0)

        can, reason = safety.can_trade()
        assert can is False
        assert "連続損失" in reason

    def test_winning_trade_resets_consecutive_losses(self, safety_setup: tuple) -> None:
        """利益トレードが連続損失カウンターをリセットすること。"""
        safety, _ = safety_setup

        # 4回連続損失の後、1回利益
        for _ in range(4):
            safety.record_trade(pnl=-50.0)
        safety.record_trade(pnl=100.0)

        assert safety._consecutive_losses == 0

    def test_reset_daily_clears_counters(self, safety_setup: tuple) -> None:
        """日次リセットが全カウンターをクリアすること。"""
        safety, _ = safety_setup

        safety.record_trade(pnl=-50.0)
        safety.record_trade(pnl=-50.0)
        safety.record_trade(pnl=100.0)

        safety.reset_daily()

        assert safety._daily_trade_count == 0
        assert safety._daily_loss_count == 0
        assert safety._consecutive_losses == 0
        assert safety._daily_pnl_total == 0.0
