"""ポートフォリオマネージャーのユニットテスト。

ポジションの開閉、損益計算、注文バリデーション、
最大ポジション数制限、状態の保存/復元をテストする。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from stella.core.portfolio import PortfolioManager, Position


class TestOpenClosePosition:
    """ポジション開閉と損益計算のテスト。"""

    def test_open_buy_position(self) -> None:
        """買いポジションを正常に開設できること。"""
        pm = PortfolioManager(initial_balance=10000.0)
        pos = pm.open_position(
            symbol="BTC/USDT",
            side="buy",
            entry_price=40000.0,
            quantity=0.1,
            strategy_name="trend_follow",
            stop_loss_price=39000.0,
        )

        assert pos.is_open is True
        assert pos.symbol == "BTC/USDT"
        assert pos.side == "buy"
        assert pos.entry_price == 40000.0
        assert pos.quantity == 0.1
        assert pos.stop_loss_price == 39000.0
        assert len(pm.get_positions()) == 1

    def test_close_buy_position_with_profit(self) -> None:
        """買いポジションを利益確定でクローズし、PnLが正しく計算されること。"""
        pm = PortfolioManager(initial_balance=10000.0)
        pos = pm.open_position(
            symbol="BTC/USDT",
            side="buy",
            entry_price=40000.0,
            quantity=0.1,
            strategy_name="trend_follow",
        )

        closed = pm.close_position(pos.position_id, exit_price=42000.0)

        # PnL = (42000 - 40000) * 0.1 = 200.0
        assert closed.realized_pnl == pytest.approx(200.0)
        assert closed.is_open is False
        assert pm._balance == pytest.approx(10200.0)
        assert len(pm.get_positions()) == 0

    def test_close_buy_position_with_loss(self) -> None:
        """買いポジションを損切りでクローズし、損失が正しく計算されること。"""
        pm = PortfolioManager(initial_balance=10000.0)
        pos = pm.open_position(
            symbol="BTC/USDT",
            side="buy",
            entry_price=40000.0,
            quantity=0.1,
            strategy_name="trend_follow",
        )

        closed = pm.close_position(pos.position_id, exit_price=38000.0)

        # PnL = (38000 - 40000) * 0.1 = -200.0
        assert closed.realized_pnl == pytest.approx(-200.0)
        assert pm._balance == pytest.approx(9800.0)

    def test_close_sell_position_with_profit(self) -> None:
        """売りポジションを利益確定でクローズし、PnLが正しく計算されること。"""
        pm = PortfolioManager(initial_balance=10000.0)
        pos = pm.open_position(
            symbol="ETH/USDT",
            side="sell",
            entry_price=3000.0,
            quantity=1.0,
            strategy_name="trend_follow",
        )

        closed = pm.close_position(pos.position_id, exit_price=2800.0)

        # PnL = (3000 - 2800) * 1.0 = 200.0 (売りなので下落で利益)
        assert closed.realized_pnl == pytest.approx(200.0)

    def test_close_sell_position_with_loss(self) -> None:
        """売りポジションが逆行した場合に損失が正しく計算されること。"""
        pm = PortfolioManager(initial_balance=10000.0)
        pos = pm.open_position(
            symbol="ETH/USDT",
            side="sell",
            entry_price=3000.0,
            quantity=1.0,
            strategy_name="trend_follow",
        )

        closed = pm.close_position(pos.position_id, exit_price=3200.0)

        # PnL = (3000 - 3200) * 1.0 = -200.0
        assert closed.realized_pnl == pytest.approx(-200.0)

    def test_close_nonexistent_position_raises(self) -> None:
        """存在しないポジションIDでクローズするとKeyErrorが発生すること。"""
        pm = PortfolioManager(initial_balance=10000.0)

        with pytest.raises(KeyError):
            pm.close_position("nonexistent-id", exit_price=40000.0)

    def test_open_position_with_invalid_side_raises(self) -> None:
        """無効なsideを指定するとValueErrorが発生すること。"""
        pm = PortfolioManager(initial_balance=10000.0)

        with pytest.raises(ValueError, match="無効なside"):
            pm.open_position(
                symbol="BTC/USDT",
                side="invalid",
                entry_price=40000.0,
                quantity=0.1,
                strategy_name="trend_follow",
            )

    def test_open_position_with_invalid_price_raises(self) -> None:
        """価格または数量が0以下の場合にValueErrorが発生すること。"""
        pm = PortfolioManager(initial_balance=10000.0)

        with pytest.raises(ValueError):
            pm.open_position(
                symbol="BTC/USDT",
                side="buy",
                entry_price=0.0,
                quantity=0.1,
                strategy_name="trend_follow",
            )


class TestValidateOrder:
    """注文バリデーションのテスト。"""

    def test_valid_order_passes(self) -> None:
        """十分な残高で正常な注文がバリデーションを通過すること。"""
        pm = PortfolioManager(initial_balance=10000.0, max_positions=3)

        is_valid, reason = pm.validate_order(
            symbol="BTC/USDT",
            side="buy",
            quantity=0.01,
            price=40000.0,
        )

        assert is_valid is True
        assert reason == "OK"

    def test_insufficient_balance_rejected(self) -> None:
        """残高不足の場合に注文が拒否されること。"""
        pm = PortfolioManager(initial_balance=100.0, max_positions=3)

        is_valid, reason = pm.validate_order(
            symbol="BTC/USDT",
            side="buy",
            quantity=0.1,
            price=40000.0,
        )

        # 注文額 = 40000 * 0.1 = 4000 > 残高100
        assert is_valid is False
        assert "残高不足" in reason

    def test_max_positions_rejected(self) -> None:
        """最大ポジション数に達している場合に新規注文が拒否されること。"""
        pm = PortfolioManager(initial_balance=100000.0, max_positions=2)

        # 2つのポジションを開設して上限に達する
        pm.open_position("BTC/USDT", "buy", 40000.0, 0.01, "trend_follow")
        pm.open_position("ETH/USDT", "buy", 3000.0, 0.1, "trend_follow")

        is_valid, reason = pm.validate_order(
            symbol="SOL/USDT",
            side="buy",
            quantity=1.0,
            price=100.0,
        )

        assert is_valid is False
        assert "最大ポジション数" in reason

    def test_duplicate_symbol_side_rejected(self) -> None:
        """同一シンボル・同一方向の重複ポジションが拒否されること。"""
        pm = PortfolioManager(initial_balance=100000.0, max_positions=5)
        pm.open_position("BTC/USDT", "buy", 40000.0, 0.01, "trend_follow")

        is_valid, reason = pm.validate_order(
            symbol="BTC/USDT",
            side="buy",
            quantity=0.01,
            price=40000.0,
        )

        assert is_valid is False
        assert "既に存在" in reason

    def test_exposure_limit_rejected(self) -> None:
        """エクスポージャー上限を超える注文が拒否されること。"""
        pm = PortfolioManager(
            initial_balance=10000.0,
            max_positions=5,
            max_exposure_ratio=0.3,
        )

        # エクスポージャー上限 = 10000 * 0.3 = 3000
        is_valid, reason = pm.validate_order(
            symbol="BTC/USDT",
            side="buy",
            quantity=0.1,
            price=40000.0,  # 注文額 = 4000 > 3000
        )

        assert is_valid is False
        assert "エクスポージャー" in reason


class TestMaxPositionsLimit:
    """最大ポジション数制限のテスト。"""

    def test_positions_up_to_limit_allowed(self) -> None:
        """最大ポジション数までは開設できること。"""
        pm = PortfolioManager(initial_balance=100000.0, max_positions=3)

        pm.open_position("BTC/USDT", "buy", 40000.0, 0.01, "trend_follow")
        pm.open_position("ETH/USDT", "buy", 3000.0, 0.1, "trend_follow")
        pm.open_position("SOL/USDT", "buy", 100.0, 1.0, "trend_follow")

        assert len(pm.get_positions()) == 3

    def test_close_then_reopen_allowed(self) -> None:
        """ポジションをクローズした後に新規ポジションを開設できること。"""
        pm = PortfolioManager(initial_balance=100000.0, max_positions=1)

        pos = pm.open_position("BTC/USDT", "buy", 40000.0, 0.01, "trend_follow")
        pm.close_position(pos.position_id, 41000.0)

        # クローズ後は枠が空くので新しいポジションを開設できる
        is_valid, _ = pm.validate_order("ETH/USDT", "buy", 0.1, 3000.0)
        assert is_valid is True


class TestSaveLoadState:
    """状態の保存・復元のテスト。"""

    def test_save_and_load_preserves_state(self, tmp_path: Path) -> None:
        """保存した状態を正しく復元できること。"""
        pm = PortfolioManager(initial_balance=10000.0, max_positions=3)
        pm.open_position("BTC/USDT", "buy", 40000.0, 0.1, "trend_follow")

        state_file = tmp_path / "portfolio_state.json"
        pm.save_state(state_file)

        # 新しいインスタンスで復元
        pm2 = PortfolioManager()
        pm2.load_state(state_file)

        assert pm2._balance == pytest.approx(10000.0)
        assert pm2._max_positions == 3
        assert len(pm2._positions) == 1

        # 復元したポジションの属性を確認
        restored_pos = list(pm2._positions.values())[0]
        assert restored_pos.symbol == "BTC/USDT"
        assert restored_pos.entry_price == pytest.approx(40000.0)

    def test_load_nonexistent_file_raises(self, tmp_path: Path) -> None:
        """存在しないファイルから復元するとFileNotFoundErrorが発生すること。"""
        pm = PortfolioManager()

        with pytest.raises(FileNotFoundError):
            pm.load_state(tmp_path / "nonexistent.json")

    def test_save_creates_parent_directories(self, tmp_path: Path) -> None:
        """保存先の親ディレクトリが自動作成されること。"""
        pm = PortfolioManager(initial_balance=5000.0)
        state_file = tmp_path / "nested" / "dir" / "state.json"

        pm.save_state(state_file)

        assert state_file.exists()
        data = json.loads(state_file.read_text())
        assert data["balance"] == pytest.approx(5000.0)

    def test_closed_positions_preserved(self, tmp_path: Path) -> None:
        """クローズ済みポジションも保存・復元されること。"""
        pm = PortfolioManager(initial_balance=10000.0)
        pos = pm.open_position("BTC/USDT", "buy", 40000.0, 0.1, "trend_follow")
        pm.close_position(pos.position_id, 42000.0)

        state_file = tmp_path / "state.json"
        pm.save_state(state_file)

        pm2 = PortfolioManager()
        pm2.load_state(state_file)

        assert len(pm2._closed_positions) == 1
        assert pm2._closed_positions[0].realized_pnl == pytest.approx(200.0)


class TestDailyPnlTracking:
    """日次損益追跡のテスト。"""

    def test_daily_pnl_calculation(self) -> None:
        """日次PnLが残高変動から正しく算出されること。"""
        pm = PortfolioManager(initial_balance=10000.0)

        # 開始時点ではdaily_pnlは0
        assert pm.daily_pnl == pytest.approx(0.0)

        # ポジションを開閉して利益を出す
        pos = pm.open_position("BTC/USDT", "buy", 40000.0, 0.1, "trend_follow")
        pm.close_position(pos.position_id, 41000.0)

        # PnL = (41000 - 40000) * 0.1 = 100.0
        assert pm.daily_pnl == pytest.approx(100.0)

    def test_reset_daily(self) -> None:
        """日次リセットでdaily_start_balanceが更新されること。"""
        pm = PortfolioManager(initial_balance=10000.0)

        # 利益を出す
        pos = pm.open_position("BTC/USDT", "buy", 40000.0, 0.1, "trend_follow")
        pm.close_position(pos.position_id, 41000.0)

        assert pm.daily_pnl == pytest.approx(100.0)

        # 日次リセット
        pm.reset_daily()

        # リセット後はdaily_pnlが0になる
        assert pm.daily_pnl == pytest.approx(0.0)

    def test_total_pnl_accumulates(self) -> None:
        """total_pnlが複数トレードの実現損益を累積すること。"""
        pm = PortfolioManager(initial_balance=10000.0, max_positions=3)

        # 利益トレード
        pos1 = pm.open_position("BTC/USDT", "buy", 40000.0, 0.1, "trend_follow")
        pm.close_position(pos1.position_id, 41000.0)  # +100

        # 損失トレード
        pos2 = pm.open_position("ETH/USDT", "buy", 3000.0, 1.0, "trend_follow")
        pm.close_position(pos2.position_id, 2950.0)  # -50

        assert pm.total_pnl == pytest.approx(50.0)

    def test_peak_balance_tracks_highest(self) -> None:
        """ピーク残高が最高値を正しく追跡すること。"""
        pm = PortfolioManager(initial_balance=10000.0)

        # 利益で残高増加 -> ピーク更新
        pos = pm.open_position("BTC/USDT", "buy", 40000.0, 0.1, "trend_follow")
        pm.close_position(pos.position_id, 42000.0)
        assert pm._peak_balance == pytest.approx(10200.0)

        # 損失で残高減少 -> ピークは維持される
        pos2 = pm.open_position("ETH/USDT", "buy", 3000.0, 1.0, "trend_follow")
        pm.close_position(pos2.position_id, 2900.0)
        assert pm._peak_balance == pytest.approx(10200.0)  # ピーク維持
        assert pm._balance == pytest.approx(10100.0)
