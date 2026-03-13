"""安全機構モジュール。

日次損失制限、最大ドローダウン、ボラティリティチェック、キルスイッチなど、
トレーディングの安全性を確保するための機構を提供する。

v1では安全機構が不十分だったため、v2では多層的な防御を実装する。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import structlog

from stella.core.portfolio import PortfolioManager

logger = structlog.get_logger(__name__)


@dataclass
class SafetyConfig:
    """安全機構の設定パラメータ。

    各閾値はデフォルトで保守的な値を設定している。
    本番運用前にバックテスト結果に基づいて調整すること。
    """

    # 日次損失上限(残高に対する割合)
    daily_loss_limit: float = 0.05

    # 最大ドローダウン(ピークからの下落率)
    max_drawdown: float = 0.20

    # ボラティリティ閾値(ATRが平均の何倍で取引停止)
    volatility_multiplier: float = 2.0

    # 1トレードあたりの最大リスク(残高に対する割合)
    max_trade_risk: float = 0.02

    # ボラティリティ一時停止後の自動再開までの秒数
    volatility_pause_duration: int = 300

    # 最大連続損失回数
    max_consecutive_losses: int = 5


class SafetyManager:
    """安全機構管理クラス。

    取引の安全性を多層的にチェックし、異常時には取引を停止する。
    キルスイッチによる緊急全ポジション決済機能も備える。

    チェック項目:
    - 日次損失上限
    - 最大ドローダウン
    - ボラティリティ(ATRベース)
    - 1トレードあたりのリスク
    - 連続損失回数
    - 手動一時停止
    """

    def __init__(
        self,
        config: SafetyConfig,
        portfolio: PortfolioManager,
    ) -> None:
        """SafetyManagerを初期化する。

        Args:
            config: 安全機構の設定
            portfolio: ポートフォリオマネージャーへの参照
        """
        self._config = config
        self._portfolio = portfolio
        self._is_paused: bool = False
        self._pause_reason: str = ""
        self._daily_trade_count: int = 0
        self._daily_loss_count: int = 0
        self._consecutive_losses: int = 0
        self._daily_pnl_total: float = 0.0
        self._volatility_paused_at: str | None = None
        self._kill_switch_activated: bool = False
        self._trade_history: list[dict[str, Any]] = []
        self._log = logger.bind(component="safety")

    def can_trade(self) -> tuple[bool, str]:
        """現在取引が可能かどうかを判定する。

        全ての安全チェックを総合的に評価し、取引可否と理由を返す。

        Returns:
            (取引可否, 理由メッセージ)のタプル
        """
        # キルスイッチが発動済みの場合
        if self._kill_switch_activated:
            return False, "キルスイッチが発動済みです。手動で解除してください"

        # 手動一時停止中の場合
        if self._is_paused:
            # ボラティリティによる自動一時停止の場合、期間経過で自動再開
            if self._volatility_paused_at is not None:
                if self._check_volatility_auto_resume():
                    self._log.info("ボラティリティ一時停止から自動再開")
                    self.resume()
                else:
                    return False, f"一時停止中: {self._pause_reason}"
            else:
                return False, f"一時停止中: {self._pause_reason}"

        # 日次損失チェック
        portfolio_state = self._portfolio.get_portfolio_state()
        daily_pnl = portfolio_state["daily_pnl"]
        balance = portfolio_state["balance"]

        if not self.check_daily_loss(daily_pnl, balance):
            reason = (
                f"日次損失上限に到達: {daily_pnl:.2f} "
                f"(上限: {balance * self._config.daily_loss_limit:.2f})"
            )
            self._log.warning("取引停止", reason=reason)
            return False, reason

        # ドローダウンチェック
        peak_balance = portfolio_state["peak_balance"]
        current_balance = portfolio_state["total_balance"]

        if not self.check_drawdown(current_balance, peak_balance):
            drawdown = (
                (peak_balance - current_balance) / peak_balance * 100
                if peak_balance > 0
                else 0
            )
            reason = (
                f"最大ドローダウン超過: {drawdown:.1f}% "
                f"(上限: {self._config.max_drawdown * 100:.1f}%)"
            )
            self._log.warning("取引停止", reason=reason)
            return False, reason

        # 連続損失チェック
        if self._consecutive_losses >= self._config.max_consecutive_losses:
            reason = (
                f"連続損失回数が上限に到達: {self._consecutive_losses}回 "
                f"(上限: {self._config.max_consecutive_losses}回)"
            )
            self._log.warning("取引停止", reason=reason)
            return False, reason

        return True, "取引可能"

    def check_daily_loss(self, daily_pnl: float, total_balance: float) -> bool:
        """日次損失上限をチェックする。

        Args:
            daily_pnl: 当日の損益
            total_balance: 現在の総残高

        Returns:
            損失が上限以内の場合True
        """
        if total_balance <= 0:
            return False

        loss_limit = total_balance * self._config.daily_loss_limit
        # daily_pnlが負の値(損失)の場合、その絶対値が上限を超えていないかチェック
        return daily_pnl >= -loss_limit

    def check_drawdown(self, current_balance: float, peak_balance: float) -> bool:
        """最大ドローダウンをチェックする。

        Args:
            current_balance: 現在の残高
            peak_balance: ピーク残高

        Returns:
            ドローダウンが上限以内の場合True
        """
        if peak_balance <= 0:
            return False

        drawdown = (peak_balance - current_balance) / peak_balance
        return drawdown < self._config.max_drawdown

    def check_volatility(self, atr: float, avg_atr: float) -> bool:
        """ATRベースのボラティリティチェックを行う。

        現在のATRが平均ATRの指定倍率を超えた場合、高ボラティリティと判定する。

        Args:
            atr: 現在のATR値
            avg_atr: 平均ATR値

        Returns:
            ボラティリティが許容範囲内の場合True
        """
        if avg_atr <= 0:
            return False

        ratio = atr / avg_atr
        is_safe = ratio < self._config.volatility_multiplier

        if not is_safe:
            self._log.warning(
                "高ボラティリティを検知",
                atr=atr,
                avg_atr=avg_atr,
                ratio=f"{ratio:.2f}x",
                threshold=f"{self._config.volatility_multiplier}x",
            )
            # ボラティリティによる自動一時停止
            self.pause(
                f"高ボラティリティ検知: ATR={atr:.4f} "
                f"(平均の{ratio:.1f}倍)"
            )
            self._volatility_paused_at = datetime.now(timezone.utc).isoformat()

        return is_safe

    def check_trade_risk(self, risk_amount: float, total_balance: float) -> bool:
        """1トレードあたりのリスク量をチェックする。

        Args:
            risk_amount: トレードのリスク額(ストップロスまでの損失額)
            total_balance: 現在の総残高

        Returns:
            リスクが許容範囲内の場合True
        """
        if total_balance <= 0:
            return False

        max_risk = total_balance * self._config.max_trade_risk
        is_safe = risk_amount <= max_risk

        if not is_safe:
            self._log.warning(
                "1トレードリスク上限超過",
                risk_amount=risk_amount,
                max_risk=max_risk,
                balance=total_balance,
            )

        return is_safe

    async def kill_switch(self, exchange: Any, portfolio: PortfolioManager) -> list[dict[str, Any]]:
        """緊急キルスイッチ: 全ポジションを即時決済する。

        異常事態が発生した場合に呼び出し、全ポジションを成行注文でクローズする。
        一度発動すると手動解除するまで取引を停止する。

        Args:
            exchange: ccxt互換の取引所インスタンス
            portfolio: ポートフォリオマネージャー

        Returns:
            決済結果のリスト
        """
        self._kill_switch_activated = True
        self._is_paused = True
        self._pause_reason = "キルスイッチ発動"

        self._log.critical(
            "キルスイッチ発動 - 全ポジション決済開始",
        )

        results: list[dict[str, Any]] = []
        positions = portfolio.get_positions()

        for position in positions:
            result: dict[str, Any] = {
                "position_id": position.position_id,
                "symbol": position.symbol,
                "side": position.side,
                "quantity": position.quantity,
                "success": False,
            }

            try:
                # 反対方向の成行注文で決済
                close_side = "sell" if position.side == "buy" else "buy"
                order = await exchange.create_market_order(
                    position.symbol,
                    close_side,
                    position.quantity,
                )

                exit_price = float(order.get("average", order.get("price", 0)))
                portfolio.close_position(position.position_id, exit_price)

                result["success"] = True
                result["exit_price"] = exit_price
                result["order_id"] = order.get("id")

                self._log.info(
                    "ポジション強制決済完了",
                    position_id=position.position_id,
                    symbol=position.symbol,
                    exit_price=exit_price,
                )

            except Exception as e:
                result["error"] = str(e)
                self._log.error(
                    "ポジション強制決済に失敗",
                    position_id=position.position_id,
                    symbol=position.symbol,
                    error=str(e),
                )

            results.append(result)

        self._log.critical(
            "キルスイッチ処理完了",
            total=len(positions),
            success=sum(1 for r in results if r["success"]),
            failed=sum(1 for r in results if not r["success"]),
        )

        return results

    def record_trade(self, pnl: float) -> None:
        """トレード結果を記録する。

        日次の損益追跡と連続損失カウントを更新する。

        Args:
            pnl: トレードの損益
        """
        self._daily_trade_count += 1
        self._daily_pnl_total += pnl

        if pnl < 0:
            self._daily_loss_count += 1
            self._consecutive_losses += 1
        else:
            self._consecutive_losses = 0

        self._trade_history.append({
            "pnl": pnl,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "daily_trade_number": self._daily_trade_count,
        })

        self._log.info(
            "トレード結果を記録",
            pnl=pnl,
            daily_trade_count=self._daily_trade_count,
            consecutive_losses=self._consecutive_losses,
        )

    def reset_daily(self) -> None:
        """日次カウンターをリセットする。

        毎日0時(UTC)に呼び出し、日次の統計情報を初期化する。
        """
        self._daily_trade_count = 0
        self._daily_loss_count = 0
        self._daily_pnl_total = 0.0
        self._consecutive_losses = 0
        self._trade_history.clear()

        self._log.info("日次安全カウンターをリセット")

    def pause(self, reason: str) -> None:
        """取引を手動で一時停止する。

        Args:
            reason: 停止理由
        """
        self._is_paused = True
        self._pause_reason = reason

        self._log.warning("取引を一時停止", reason=reason)

    def resume(self) -> None:
        """取引の一時停止を解除する。

        キルスイッチが発動済みの場合も解除する。
        """
        self._is_paused = False
        self._pause_reason = ""
        self._volatility_paused_at = None
        self._kill_switch_activated = False

        self._log.info("取引を再開")

    def get_state(self) -> dict[str, Any]:
        """安全機構の現在の状態を辞書形式で返す。

        API/ダッシュボード連携用。

        Returns:
            安全機構の状態辞書
        """
        can_trade, reason = self.can_trade()

        return {
            "can_trade": can_trade,
            "reason": reason,
            "is_paused": self._is_paused,
            "pause_reason": self._pause_reason,
            "kill_switch_activated": self._kill_switch_activated,
            "daily_trade_count": self._daily_trade_count,
            "daily_loss_count": self._daily_loss_count,
            "consecutive_losses": self._consecutive_losses,
            "daily_pnl_total": self._daily_pnl_total,
            "volatility_paused_at": self._volatility_paused_at,
            "config": {
                "daily_loss_limit": self._config.daily_loss_limit,
                "max_drawdown": self._config.max_drawdown,
                "volatility_multiplier": self._config.volatility_multiplier,
                "max_trade_risk": self._config.max_trade_risk,
                "volatility_pause_duration": self._config.volatility_pause_duration,
                "max_consecutive_losses": self._config.max_consecutive_losses,
            },
        }

    # -- 状態永続化 --

    def save_state(self, path: str | Path) -> None:
        """安全機構の状態をJSONファイルに永続化する。

        Args:
            path: 保存先ファイルパス
        """
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)

        state = {
            "is_paused": self._is_paused,
            "pause_reason": self._pause_reason,
            "kill_switch_activated": self._kill_switch_activated,
            "daily_trade_count": self._daily_trade_count,
            "daily_loss_count": self._daily_loss_count,
            "consecutive_losses": self._consecutive_losses,
            "daily_pnl_total": self._daily_pnl_total,
            "volatility_paused_at": self._volatility_paused_at,
            "trade_history": self._trade_history,
        }

        path.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
        self._log.info("安全機構の状態を保存", path=str(path))

    def load_state(self, path: str | Path) -> None:
        """JSONファイルから安全機構の状態を復元する。

        Args:
            path: 読み込むファイルパス

        Raises:
            FileNotFoundError: ファイルが存在しない場合
        """
        path = Path(path)

        if not path.exists():
            raise FileNotFoundError(f"状態ファイルが見つかりません: {path}")

        data = json.loads(path.read_text(encoding="utf-8"))

        self._is_paused = data.get("is_paused", False)
        self._pause_reason = data.get("pause_reason", "")
        self._kill_switch_activated = data.get("kill_switch_activated", False)
        self._daily_trade_count = data.get("daily_trade_count", 0)
        self._daily_loss_count = data.get("daily_loss_count", 0)
        self._consecutive_losses = data.get("consecutive_losses", 0)
        self._daily_pnl_total = data.get("daily_pnl_total", 0.0)
        self._volatility_paused_at = data.get("volatility_paused_at")
        self._trade_history = data.get("trade_history", [])

        self._log.info("安全機構の状態を復元", path=str(path))

    # -- 内部メソッド --

    def _check_volatility_auto_resume(self) -> bool:
        """ボラティリティ一時停止からの自動再開条件を確認する。

        Returns:
            自動再開可能な場合True
        """
        if self._volatility_paused_at is None:
            return False

        paused_at = datetime.fromisoformat(self._volatility_paused_at)
        now = datetime.now(timezone.utc)
        elapsed = (now - paused_at).total_seconds()

        return elapsed >= self._config.volatility_pause_duration
