"""FastAPIサーバーモジュール。

トレーディングダッシュボード用のREST APIエンドポイントを提供する。
ポートフォリオ状態、トレード履歴、安全機構の監視・制御が可能。
"""

from __future__ import annotations

from typing import Any

import structlog
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware

from stella.core.engine import TradingEngine

logger = structlog.get_logger(__name__)


def create_app(engine: TradingEngine) -> FastAPI:
    """FastAPIアプリケーションを作成する。

    トレーディングエンジンのインスタンスを受け取り、
    ダッシュボード用のREST APIエンドポイントを設定する。

    Args:
        engine: トレーディングエンジンのインスタンス

    Returns:
        設定済みのFastAPIアプリケーション
    """
    app = FastAPI(
        title="Stella Trader API",
        description="仮想通貨自動売買システムのダッシュボードAPI",
        version="2.0.0",
    )

    # CORSミドルウェアの設定(ダッシュボードからのアクセスを許可)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/api/portfolio")
    async def get_portfolio() -> dict[str, Any]:
        """ポートフォリオの現在の状態を取得する。

        残高、ポジション、損益情報を含むポートフォリオの全体像を返す。

        Returns:
            ポートフォリオ状態の辞書
        """
        portfolio = engine._portfolio
        if portfolio is None:
            return {"error": "ポートフォリオが初期化されていません", "status": "not_initialized"}

        state = portfolio.get_portfolio_state()
        logger.debug("ポートフォリオ状態を返却します")
        return state

    @app.get("/api/trades")
    async def get_trades(
        limit: int = Query(default=50, ge=1, le=500, description="取得件数"),
        offset: int = Query(default=0, ge=0, description="オフセット"),
    ) -> dict[str, Any]:
        """トレード履歴を取得する。

        クローズ済みポジションの履歴をページネーション付きで返す。

        Args:
            limit: 1ページあたりの取得件数(1-500)
            offset: 取得開始位置

        Returns:
            トレード履歴とページネーション情報
        """
        portfolio = engine._portfolio
        if portfolio is None:
            return {"error": "ポートフォリオが初期化されていません", "trades": [], "total": 0}

        closed_positions = portfolio._closed_positions
        total = len(closed_positions)

        # ページネーション適用(新しい順)
        sorted_positions = list(reversed(closed_positions))
        page = sorted_positions[offset : offset + limit]

        trades = [pos.to_dict() for pos in page]

        logger.debug("トレード履歴を返却します", total=total, limit=limit, offset=offset)
        return {
            "trades": trades,
            "total": total,
            "limit": limit,
            "offset": offset,
            "has_more": offset + limit < total,
        }

    @app.get("/api/strategies")
    async def get_strategies() -> dict[str, Any]:
        """登録されている戦略の状態を取得する。

        各戦略の名前、有効/無効状態、クールダウン情報を返す。

        Returns:
            戦略状態のリストを含む辞書
        """
        strategies = engine._strategies
        strategy_states = []

        for strategy in strategies:
            state = {
                "name": strategy.name,
                "is_active": strategy.is_active,
                "is_cooldown": strategy.is_cooldown(),
            }
            strategy_states.append(state)

        logger.debug("戦略状態を返却します", count=len(strategy_states))
        return {"strategies": strategy_states}

    @app.get("/api/safety")
    async def get_safety() -> dict[str, Any]:
        """安全機構の現在の状態を取得する。

        キルスイッチ、一時停止、日次損失、連続損失などの状態を返す。

        Returns:
            安全機構の状態辞書
        """
        safety = engine._safety
        if safety is None:
            return {"error": "安全機構が初期化されていません", "status": "not_initialized"}

        state = safety.get_state()
        logger.debug("安全機構の状態を返却します")
        return state

    @app.get("/api/stats")
    async def get_stats() -> dict[str, Any]:
        """パフォーマンス統計を取得する。

        シャープレシオ、ドローダウン、勝率などの統計情報を算出して返す。

        Returns:
            パフォーマンス統計の辞書
        """
        portfolio = engine._portfolio
        if portfolio is None:
            return {"error": "ポートフォリオが初期化されていません"}

        state = portfolio.get_portfolio_state()
        closed = portfolio._closed_positions

        # 基本統計
        total_trades = len(closed)
        winning_trades = [p for p in closed if p.realized_pnl > 0]
        losing_trades = [p for p in closed if p.realized_pnl <= 0]

        win_rate = (
            (len(winning_trades) / total_trades * 100.0) if total_trades > 0 else 0.0
        )

        # プロフィットファクター
        gross_profit = sum(p.realized_pnl for p in winning_trades)
        gross_loss = abs(sum(p.realized_pnl for p in losing_trades))
        if gross_loss > 0:
            profit_factor = gross_profit / gross_loss
        else:
            profit_factor = float("inf") if gross_profit > 0 else 0.0

        # ドローダウン
        peak_balance = state["peak_balance"]
        current_balance = state["total_balance"]
        drawdown_pct = (
            (peak_balance - current_balance) / peak_balance * 100.0
            if peak_balance > 0
            else 0.0
        )

        # 平均損益
        avg_pnl = (
            sum(p.realized_pnl for p in closed) / total_trades
            if total_trades > 0
            else 0.0
        )

        stats = {
            "total_trades": total_trades,
            "winning_trades": len(winning_trades),
            "losing_trades": len(losing_trades),
            "win_rate": round(win_rate, 2),
            "profit_factor": round(profit_factor, 4) if profit_factor != float("inf") else "inf",
            "total_pnl": round(state["total_pnl"], 2),
            "daily_pnl": round(state["daily_pnl"], 2),
            "current_drawdown_pct": round(drawdown_pct, 2),
            "peak_balance": round(peak_balance, 2),
            "avg_pnl_per_trade": round(avg_pnl, 2),
            "gross_profit": round(gross_profit, 2),
            "gross_loss": round(gross_loss, 2),
        }

        logger.debug("パフォーマンス統計を返却します")
        return stats

    @app.post("/api/safety/kill-switch")
    async def trigger_kill_switch() -> dict[str, Any]:
        """キルスイッチを発動する。

        全ポジションを即時決済し、取引を停止する。

        Returns:
            キルスイッチ発動結果
        """
        safety = engine._safety
        portfolio = engine._portfolio
        exchange = engine._exchange

        if safety is None or portfolio is None:
            return {"error": "エンジンが初期化されていません", "success": False}

        logger.warning("APIからキルスイッチが発動されました")

        if exchange is not None:
            results = await safety.kill_switch(exchange, portfolio)
            return {
                "success": True,
                "message": "キルスイッチを発動しました",
                "results": results,
            }
        else:
            # 取引所未接続の場合は安全機構のみ更新
            safety._kill_switch_activated = True
            safety._is_paused = True
            safety._pause_reason = "キルスイッチ発動(API経由)"
            return {
                "success": True,
                "message": "キルスイッチを発動しました(取引所未接続のためポジション決済はスキップ)",
            }

    @app.post("/api/safety/pause")
    async def pause_trading() -> dict[str, Any]:
        """取引を一時停止する。

        Returns:
            一時停止結果
        """
        safety = engine._safety
        if safety is None:
            return {"error": "安全機構が初期化されていません", "success": False}

        safety.pause("API経由で手動一時停止")
        logger.info("APIから取引を一時停止しました")
        return {"success": True, "message": "取引を一時停止しました"}

    @app.post("/api/safety/resume")
    async def resume_trading() -> dict[str, Any]:
        """取引を再開する。

        キルスイッチが発動済みの場合も解除する。

        Returns:
            再開結果
        """
        safety = engine._safety
        if safety is None:
            return {"error": "安全機構が初期化されていません", "success": False}

        safety.resume()
        logger.info("APIから取引を再開しました")
        return {"success": True, "message": "取引を再開しました"}

    return app
