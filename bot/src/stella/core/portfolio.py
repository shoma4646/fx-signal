"""ポートフォリオ管理モジュール。

全戦略のポジションを統合管理し、実残高との同期・リスクバリデーションを行う。
v1で発生した状態と実残高の乖離問題を根本的に解決するための中核コンポーネント。
"""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


@dataclass
class Position:
    """個別ポジションを表すデータクラス。

    各戦略が保有するポジションの状態を管理する。
    エントリーからクローズまでのライフサイクル全体を追跡する。
    """

    symbol: str
    side: str  # "buy" または "sell"
    entry_price: float
    quantity: float
    timestamp: str
    strategy_name: str
    stop_loss_price: float | None = None
    trailing_stop_price: float | None = None
    unrealized_pnl: float = 0.0
    realized_pnl: float = 0.0
    position_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    is_open: bool = True

    def to_dict(self) -> dict[str, Any]:
        """ポジションを辞書形式に変換する。"""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Position:
        """辞書からPositionインスタンスを生成する。"""
        return cls(**data)


class PortfolioManager:
    """ポートフォリオ統合管理クラス。

    全戦略のポジションを一元管理し、注文前のバリデーション、
    実残高との同期、リスクチェックを担当する。

    v1の最大の問題であった状態と実残高の乖離を防ぐため、
    注文前に必ず実残高を確認し、乖離検知時は即座にアラートを発する。
    """

    def __init__(
        self,
        initial_balance: float = 0.0,
        max_positions: int = 3,
        max_exposure_ratio: float = 0.5,
        max_risk_per_trade: float = 0.02,
    ) -> None:
        """PortfolioManagerを初期化する。

        Args:
            initial_balance: 初期残高
            max_positions: 最大同時ポジション数
            max_exposure_ratio: 最大エクスポージャー比率(残高に対する割合)
            max_risk_per_trade: 1トレードあたりの最大リスク比率
        """
        self._positions: dict[str, Position] = {}
        self._closed_positions: list[Position] = []
        self._balance: float = initial_balance
        self._peak_balance: float = initial_balance
        self._daily_start_balance: float = initial_balance
        self._max_positions: int = max_positions
        self._max_exposure_ratio: float = max_exposure_ratio
        self._max_risk_per_trade: float = max_risk_per_trade
        self._last_sync_time: str | None = None
        self._log = logger.bind(component="portfolio")

    # -- プロパティ --

    @property
    def total_balance(self) -> float:
        """現在の総残高(未実現損益含む)を返す。"""
        return self._balance + sum(
            p.unrealized_pnl for p in self._positions.values()
        )

    @property
    def total_pnl(self) -> float:
        """全期間の実現損益合計を返す。"""
        return sum(p.realized_pnl for p in self._closed_positions)

    @property
    def daily_pnl(self) -> float:
        """当日の損益を返す。"""
        return self.total_balance - self._daily_start_balance

    # -- 取引所同期 --

    async def sync_with_exchange(self, exchange: Any) -> dict[str, Any]:
        """取引所の実残高とポジションを同期する。

        実残高と内部状態の乖離を検知した場合はアラートを発する。
        v1で発生した¥38,000損失の再発を防ぐための最重要メソッド。

        Args:
            exchange: ccxt互換の取引所インスタンス

        Returns:
            同期結果を含む辞書。乖離があった場合はdiscrepancyキーにTrue。
        """
        result: dict[str, Any] = {
            "synced": False,
            "discrepancy": False,
            "details": {},
        }

        try:
            # 取引所から残高を取得
            balance_info = await exchange.get_balance()

            # 取引所名から基軸通貨を判定
            exchange_name = getattr(exchange, "name", "").lower()
            if "bitbank" in exchange_name:
                quote_currency = "JPY"
            else:
                quote_currency = "USDT"

            exchange_balance = self._extract_balance(balance_info, quote_currency)

            # 取引所からオープンポジションを取得
            exchange_positions = await exchange.get_open_orders() if hasattr(exchange, "get_open_orders") else []

            # 残高の乖離チェック
            balance_diff = abs(self._balance - exchange_balance)
            balance_threshold = self._balance * 0.01  # 1%の閾値

            if balance_diff > balance_threshold and self._balance > 0:
                self._log.warning(
                    "残高の乖離を検知",
                    internal_balance=self._balance,
                    exchange_balance=exchange_balance,
                    diff=balance_diff,
                )
                result["discrepancy"] = True
                result["details"]["balance_diff"] = balance_diff

            # 内部状態を取引所の値で更新
            self._balance = exchange_balance

            # ピーク残高の更新
            if self._balance > self._peak_balance:
                self._peak_balance = self._balance

            # ポジション数の乖離チェック
            exchange_open_count = len(
                [p for p in exchange_positions if float(p.get("contracts", 0)) > 0]
            )
            internal_open_count = len(self._positions)

            if exchange_open_count != internal_open_count:
                self._log.warning(
                    "ポジション数の乖離を検知",
                    internal_count=internal_open_count,
                    exchange_count=exchange_open_count,
                )
                result["discrepancy"] = True
                result["details"]["position_count_diff"] = (
                    exchange_open_count - internal_open_count
                )

            self._last_sync_time = datetime.now(timezone.utc).isoformat()
            result["synced"] = True

            self._log.info(
                "取引所との同期完了",
                balance=self._balance,
                open_positions=internal_open_count,
                discrepancy=result["discrepancy"],
            )

        except Exception as e:
            self._log.error("取引所同期に失敗", error=str(e))
            result["details"]["error"] = str(e)

        return result

    # -- ポジション管理 --

    def open_position(
        self,
        symbol: str,
        side: str,
        entry_price: float,
        quantity: float,
        strategy_name: str,
        stop_loss_price: float | None = None,
    ) -> Position:
        """新規ポジションを登録する。

        Args:
            symbol: 取引ペア(例: "BTC/USDT")
            side: 売買方向("buy" または "sell")
            entry_price: エントリー価格
            quantity: 数量
            strategy_name: 戦略名
            stop_loss_price: ストップロス価格

        Returns:
            登録されたPositionオブジェクト

        Raises:
            ValueError: 無効なパラメータの場合
        """
        if side not in ("buy", "sell"):
            raise ValueError(f"無効なside: {side}。'buy'または'sell'を指定してください")

        if entry_price <= 0 or quantity <= 0:
            raise ValueError("entry_priceとquantityは正の値である必要があります")

        position = Position(
            symbol=symbol,
            side=side,
            entry_price=entry_price,
            quantity=quantity,
            timestamp=datetime.now(timezone.utc).isoformat(),
            strategy_name=strategy_name,
            stop_loss_price=stop_loss_price,
        )

        self._positions[position.position_id] = position

        self._log.info(
            "ポジションを開設",
            position_id=position.position_id,
            symbol=symbol,
            side=side,
            entry_price=entry_price,
            quantity=quantity,
            strategy=strategy_name,
        )

        return position

    def close_position(self, position_id: str, exit_price: float) -> Position:
        """ポジションをクローズし、損益を計算する。

        Args:
            position_id: クローズするポジションのID
            exit_price: クローズ価格

        Returns:
            クローズされたPositionオブジェクト(realized_pnl計算済み)

        Raises:
            KeyError: 指定IDのポジションが存在しない場合
            ValueError: 既にクローズ済みの場合
        """
        if position_id not in self._positions:
            raise KeyError(f"ポジションが見つかりません: {position_id}")

        position = self._positions[position_id]

        if not position.is_open:
            raise ValueError(f"ポジションは既にクローズ済みです: {position_id}")

        # 損益計算
        if position.side == "buy":
            pnl = (exit_price - position.entry_price) * position.quantity
        else:
            pnl = (position.entry_price - exit_price) * position.quantity

        position.realized_pnl = pnl
        position.unrealized_pnl = 0.0
        position.is_open = False

        # 残高に反映
        self._balance += pnl

        # ピーク残高の更新
        if self._balance > self._peak_balance:
            self._peak_balance = self._balance

        # クローズ済みリストに移動
        del self._positions[position_id]
        self._closed_positions.append(position)

        self._log.info(
            "ポジションをクローズ",
            position_id=position_id,
            symbol=position.symbol,
            side=position.side,
            entry_price=position.entry_price,
            exit_price=exit_price,
            pnl=pnl,
        )

        return position

    def update_trailing_stop(self, position_id: str, new_stop: float) -> None:
        """トレーリングストップ価格を更新する。

        トレーリングストップは現在の方向に有利な方向にのみ更新可能。

        Args:
            position_id: 対象ポジションのID
            new_stop: 新しいトレーリングストップ価格

        Raises:
            KeyError: 指定IDのポジションが存在しない場合
        """
        if position_id not in self._positions:
            raise KeyError(f"ポジションが見つかりません: {position_id}")

        position = self._positions[position_id]
        old_stop = position.trailing_stop_price

        # トレーリングストップは有利な方向にのみ更新
        if old_stop is not None:
            if position.side == "buy" and new_stop <= old_stop:
                self._log.debug(
                    "トレーリングストップの更新をスキップ(有利方向でない)",
                    position_id=position_id,
                    old_stop=old_stop,
                    new_stop=new_stop,
                )
                return
            if position.side == "sell" and new_stop >= old_stop:
                self._log.debug(
                    "トレーリングストップの更新をスキップ(有利方向でない)",
                    position_id=position_id,
                    old_stop=old_stop,
                    new_stop=new_stop,
                )
                return

        position.trailing_stop_price = new_stop

        self._log.info(
            "トレーリングストップを更新",
            position_id=position_id,
            old_stop=old_stop,
            new_stop=new_stop,
        )

    def get_position(self, symbol: str) -> Position | None:
        """指定シンボルのオープンポジションを取得する。

        Args:
            symbol: 取引ペア(例: "BTC/JPY")

        Returns:
            該当するPositionオブジェクト。存在しない場合はNone。
        """
        for p in self._positions.values():
            if p.symbol == symbol and p.is_open:
                return p
        return None

    def get_open_positions(self) -> list[Position]:
        """全オープンポジションをリストで返す。

        Returns:
            オープンポジションのリスト
        """
        return [p for p in self._positions.values() if p.is_open]

    def get_positions(self, strategy_name: str | None = None) -> list[Position]:
        """オープンポジション一覧を取得する。

        Args:
            strategy_name: フィルタする戦略名。Noneの場合は全ポジション。

        Returns:
            オープンポジションのリスト
        """
        positions = list(self._positions.values())

        if strategy_name is not None:
            positions = [p for p in positions if p.strategy_name == strategy_name]

        return positions

    def get_total_exposure(self) -> float:
        """全オープンポジションの合計エクスポージャー(時価総額)を返す。

        Returns:
            ポジション価値の合計
        """
        return sum(p.entry_price * p.quantity for p in self._positions.values())

    def validate_order(
        self,
        symbol: str,
        side: str,
        quantity: float,
        price: float,
    ) -> tuple[bool, str]:
        """注文の安全性を検証する。

        残高チェック、リスクチェック、最大ポジション数チェックを行う。
        注文前に必ず呼び出すことで、v1の状態乖離問題を防止する。

        Args:
            symbol: 取引ペア
            side: 売買方向
            quantity: 数量
            price: 価格

        Returns:
            (検証結果, 理由メッセージ)のタプル
        """
        order_value = price * quantity

        # 最大ポジション数チェック
        if len(self._positions) >= self._max_positions:
            reason = (
                f"最大ポジション数({self._max_positions})に達しています"
            )
            self._log.warning("注文バリデーション失敗", reason=reason)
            return False, reason

        # 同一シンボル・同一方向の重複チェック
        for pos in self._positions.values():
            if pos.symbol == symbol and pos.side == side:
                reason = f"{symbol}の{side}ポジションが既に存在します"
                self._log.warning("注文バリデーション失敗", reason=reason)
                return False, reason

        # 残高チェック
        if order_value > self._balance:
            reason = (
                f"残高不足: 注文額{order_value:.2f} > 残高{self._balance:.2f}"
            )
            self._log.warning("注文バリデーション失敗", reason=reason)
            return False, reason

        # エクスポージャーチェック
        total_exposure = self.get_total_exposure() + order_value
        max_exposure = self._balance * self._max_exposure_ratio

        if total_exposure > max_exposure:
            reason = (
                f"エクスポージャー上限超過: "
                f"{total_exposure:.2f} > {max_exposure:.2f}"
            )
            self._log.warning("注文バリデーション失敗", reason=reason)
            return False, reason

        # 1トレードリスクチェック
        max_risk = self._balance * self._max_risk_per_trade
        if order_value > max_risk * 50:
            # ストップロス幅が2%の場合、注文額はリスク額の約50倍が上限
            reason = (
                f"1トレードあたりのリスク上限超過: "
                f"注文額{order_value:.2f}"
            )
            self._log.warning("注文バリデーション失敗", reason=reason)
            return False, reason

        self._log.debug(
            "注文バリデーション通過",
            symbol=symbol,
            side=side,
            quantity=quantity,
            price=price,
        )
        return True, "OK"

    # -- 状態管理 --

    def get_portfolio_state(self) -> dict[str, Any]:
        """ポートフォリオ全体の状態を辞書形式で返す。

        API/ダッシュボード連携用の完全な状態情報を提供する。

        Returns:
            ポートフォリオ状態の辞書
        """
        return {
            "balance": self._balance,
            "total_balance": self.total_balance,
            "peak_balance": self._peak_balance,
            "daily_start_balance": self._daily_start_balance,
            "total_pnl": self.total_pnl,
            "daily_pnl": self.daily_pnl,
            "total_exposure": self.get_total_exposure(),
            "open_positions": [p.to_dict() for p in self._positions.values()],
            "open_position_count": len(self._positions),
            "closed_position_count": len(self._closed_positions),
            "max_positions": self._max_positions,
            "last_sync_time": self._last_sync_time,
        }

    def save_state(self, path: str | Path) -> None:
        """ポートフォリオ状態をJSONファイルに永続化する。

        Args:
            path: 保存先ファイルパス
        """
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)

        state = {
            "balance": self._balance,
            "peak_balance": self._peak_balance,
            "daily_start_balance": self._daily_start_balance,
            "max_positions": self._max_positions,
            "max_exposure_ratio": self._max_exposure_ratio,
            "max_risk_per_trade": self._max_risk_per_trade,
            "last_sync_time": self._last_sync_time,
            "positions": {
                pid: p.to_dict() for pid, p in self._positions.items()
            },
            "closed_positions": [p.to_dict() for p in self._closed_positions],
        }

        path.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
        self._log.info("ポートフォリオ状態を保存", path=str(path))

    def load_state(self, path: str | Path) -> None:
        """JSONファイルからポートフォリオ状態を復元する。

        Args:
            path: 読み込むファイルパス

        Raises:
            FileNotFoundError: ファイルが存在しない場合
        """
        path = Path(path)

        if not path.exists():
            raise FileNotFoundError(f"状態ファイルが見つかりません: {path}")

        data = json.loads(path.read_text(encoding="utf-8"))

        self._balance = data["balance"]
        self._peak_balance = data["peak_balance"]
        self._daily_start_balance = data["daily_start_balance"]
        self._max_positions = data.get("max_positions", self._max_positions)
        self._max_exposure_ratio = data.get("max_exposure_ratio", self._max_exposure_ratio)
        self._max_risk_per_trade = data.get("max_risk_per_trade", self._max_risk_per_trade)
        self._last_sync_time = data.get("last_sync_time")

        self._positions = {
            pid: Position.from_dict(pdata)
            for pid, pdata in data.get("positions", {}).items()
        }

        self._closed_positions = [
            Position.from_dict(pdata)
            for pdata in data.get("closed_positions", [])
        ]

        self._log.info(
            "ポートフォリオ状態を復元",
            path=str(path),
            balance=self._balance,
            open_positions=len(self._positions),
        )

    def reset_daily(self) -> None:
        """日次カウンターをリセットする。日付変更時に呼び出す。"""
        self._daily_start_balance = self._balance
        self._log.info("日次カウンターをリセット", balance=self._balance)

    @staticmethod
    def _extract_balance(balance_info: Any, currency: str) -> float:
        """取引所の残高レスポンスから指定通貨の残高を抽出する。

        ccxtの残高レスポンス形式に対応:
        - {"JPY": {"total": 1000000}} (通貨キー直下)
        - {"total": {"JPY": 1000000}} (totalキー配下)
        - {"JPY": 1000000} (単純な値)

        Args:
            balance_info: 取引所APIのレスポンス
            currency: 基軸通貨 ("JPY" or "USDT")

        Returns:
            残高。取得できない場合は0.0。
        """
        if not isinstance(balance_info, dict):
            return 0.0

        # パターン1: {"JPY": {"total": ...}}
        if currency in balance_info:
            info = balance_info[currency]
            if isinstance(info, dict):
                return float(info.get("total", info.get("free", 0.0)))
            return float(info)

        # パターン2: {"total": {"JPY": ...}}
        if "total" in balance_info:
            total = balance_info["total"]
            if isinstance(total, dict) and currency in total:
                return float(total[currency])

        return 0.0
