"""Discord通知モジュール。

Webhookを利用してDiscordにトレード通知、エラー通知、
キルスイッチ警告、日次レポートを送信する。
レート制限とリトライ機能を備える。
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from datetime import datetime, timezone
from typing import Any

import aiohttp
import structlog

logger = structlog.get_logger(__name__)

# レベルに対応するDiscord Embedカラー
LEVEL_COLORS: dict[str, int] = {
    "info": 0x3498DB,      # 青
    "success": 0x2ECC71,   # 緑
    "warning": 0xF39C12,   # 橙
    "error": 0xE74C3C,     # 赤
    "critical": 0x992D22,  # 暗赤
}

# レート制限設定
RATE_LIMIT_MAX_MESSAGES = 5
RATE_LIMIT_WINDOW_SEC = 5.0

# リトライ設定
MAX_RETRIES = 2
RETRY_DELAY_SEC = 1.0


class DiscordNotifier:
    """Discord Webhook通知クラス。

    aiohttp経由でDiscord Webhookにメッセージを送信する。
    レート制限 (5秒間に最大5メッセージ) とリトライ (最大2回) を実装。

    Attributes:
        _webhook_url: Discord WebhookのURL
        _session: aiohttp ClientSession (遅延初期化)
        _send_times: レート制限管理用の送信時刻キュー
    """

    def __init__(self, webhook_url: str) -> None:
        """Discord通知を初期化する。

        Args:
            webhook_url: Discord WebhookのURL
        """
        self._webhook_url = webhook_url
        self._session: aiohttp.ClientSession | None = None
        self._send_times: deque[float] = deque(maxlen=RATE_LIMIT_MAX_MESSAGES)
        logger.info("Discord通知を初期化しました")

    async def _get_session(self) -> aiohttp.ClientSession:
        """aiohttp ClientSessionを取得する (遅延初期化)。

        Returns:
            aiohttp.ClientSession
        """
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def _wait_for_rate_limit(self) -> None:
        """レート制限を確認し、必要に応じて待機する。

        5秒間に5メッセージを超えないように制御する。
        """
        now = time.monotonic()

        # ウィンドウ外の古い送信時刻を除去
        while self._send_times and now - self._send_times[0] > RATE_LIMIT_WINDOW_SEC:
            self._send_times.popleft()

        # レート制限に達している場合は待機
        if len(self._send_times) >= RATE_LIMIT_MAX_MESSAGES:
            oldest = self._send_times[0]
            wait_time = RATE_LIMIT_WINDOW_SEC - (now - oldest)
            if wait_time > 0:
                logger.debug("レート制限により待機します", wait_sec=round(wait_time, 2))
                await asyncio.sleep(wait_time)

        self._send_times.append(time.monotonic())

    async def _post_webhook(self, payload: dict[str, Any]) -> bool:
        """Webhookにペイロードを送信する (リトライ付き)。

        Args:
            payload: Webhook送信用のJSONペイロード

        Returns:
            送信成功ならTrue
        """
        await self._wait_for_rate_limit()
        session = await self._get_session()

        for attempt in range(MAX_RETRIES + 1):
            try:
                async with session.post(
                    self._webhook_url,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as response:
                    if response.status == 204:
                        return True
                    if response.status == 429:
                        # Discord側のレート制限
                        retry_after = (await response.json()).get("retry_after", 1.0)
                        logger.warning(
                            "Discordレート制限に到達しました",
                            retry_after=retry_after,
                        )
                        await asyncio.sleep(retry_after)
                        continue
                    logger.warning(
                        "Webhook送信に失敗しました",
                        status=response.status,
                        attempt=attempt + 1,
                    )
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                logger.warning(
                    "Webhook送信中にエラーが発生しました",
                    error=str(e),
                    attempt=attempt + 1,
                )

            if attempt < MAX_RETRIES:
                await asyncio.sleep(RETRY_DELAY_SEC * (attempt + 1))

        logger.error("Webhook送信が最大リトライ回数に達しました")
        return False

    async def send(self, message: str, level: str = "info") -> bool:
        """メッセージを送信する。

        Args:
            message: 送信するメッセージ本文
            level: メッセージレベル ("info", "success", "warning", "error", "critical")

        Returns:
            送信成功ならTrue
        """
        color = LEVEL_COLORS.get(level, LEVEL_COLORS["info"])
        payload = {
            "embeds": [
                {
                    "description": message,
                    "color": color,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "footer": {"text": f"Stella Trader | {level.upper()}"},
                }
            ]
        }
        return await self._post_webhook(payload)

    async def notify_trade(
        self,
        action: str,
        symbol: str,
        price: float,
        quantity: float,
        pnl: float | None = None,
    ) -> bool:
        """トレード通知を送信する。

        Args:
            action: 売買アクション ("buy" / "sell")
            symbol: シンボル名
            price: 約定価格
            quantity: 約定数量
            pnl: 損益 (売りの場合のみ)

        Returns:
            送信成功ならTrue
        """
        if action == "buy":
            title = f"買い注文を実行 | {symbol}"
            color = LEVEL_COLORS["success"]
        else:
            title = f"売り注文を実行 | {symbol}"
            color = LEVEL_COLORS["warning"]

        fields = [
            {"name": "アクション", "value": action.upper(), "inline": True},
            {"name": "シンボル", "value": symbol, "inline": True},
            {"name": "価格", "value": f"${price:,.4f}", "inline": True},
            {"name": "数量", "value": f"{quantity:,.6f}", "inline": True},
        ]

        if pnl is not None:
            pnl_str = f"${pnl:+,.2f}"
            fields.append({"name": "損益", "value": pnl_str, "inline": True})

        payload = {
            "embeds": [
                {
                    "title": title,
                    "color": color,
                    "fields": fields,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "footer": {"text": "Stella Trader"},
                }
            ]
        }
        return await self._post_webhook(payload)

    async def notify_error(self, error: str) -> bool:
        """エラー通知を送信する。

        Args:
            error: エラーメッセージ

        Returns:
            送信成功ならTrue
        """
        payload = {
            "embeds": [
                {
                    "title": "エラーが発生しました",
                    "description": f"```\n{error}\n```",
                    "color": LEVEL_COLORS["error"],
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "footer": {"text": "Stella Trader | ERROR"},
                }
            ]
        }
        return await self._post_webhook(payload)

    async def notify_kill_switch(
        self, reason: str, closed_positions: list[dict[str, Any]]
    ) -> bool:
        """キルスイッチ発動を通知する。

        Args:
            reason: キルスイッチ発動理由
            closed_positions: 決済されたポジションのリスト

        Returns:
            送信成功ならTrue
        """
        positions_text = ""
        if closed_positions:
            lines = []
            for pos in closed_positions:
                symbol = pos.get("symbol", "N/A")
                pnl = pos.get("pnl", 0)
                lines.append(f"- {symbol}: ${pnl:+,.2f}")
            positions_text = "\n".join(lines)
        else:
            positions_text = "なし"

        payload = {
            "embeds": [
                {
                    "title": "キルスイッチが発動しました",
                    "description": f"**理由:** {reason}",
                    "color": LEVEL_COLORS["critical"],
                    "fields": [
                        {
                            "name": "決済ポジション",
                            "value": f"```\n{positions_text}\n```",
                            "inline": False,
                        }
                    ],
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "footer": {"text": "Stella Trader | KILL SWITCH"},
                }
            ]
        }
        return await self._post_webhook(payload)

    async def send_daily_report(
        self, portfolio_state: dict[str, Any], trades_today: list[dict[str, Any]]
    ) -> bool:
        """日次レポートを送信する。

        Args:
            portfolio_state: ポートフォリオの現在状態
            trades_today: 本日実行されたトレードのリスト

        Returns:
            送信成功ならTrue
        """
        balance = portfolio_state.get("balance", 0)
        total_pnl = portfolio_state.get("total_pnl", 0)
        open_positions = portfolio_state.get("open_positions", 0)
        drawdown = portfolio_state.get("drawdown_pct", 0)

        trade_count = len(trades_today)
        winning_trades = sum(1 for t in trades_today if t.get("pnl", 0) > 0)
        losing_trades = sum(1 for t in trades_today if t.get("pnl", 0) < 0)
        daily_pnl = sum(t.get("pnl", 0) for t in trades_today)

        fields = [
            {"name": "残高", "value": f"${balance:,.2f}", "inline": True},
            {"name": "本日損益", "value": f"${daily_pnl:+,.2f}", "inline": True},
            {"name": "累計損益", "value": f"${total_pnl:+,.2f}", "inline": True},
            {"name": "トレード数", "value": str(trade_count), "inline": True},
            {"name": "勝ち/負け", "value": f"{winning_trades}/{losing_trades}", "inline": True},
            {"name": "建玉数", "value": str(open_positions), "inline": True},
            {"name": "ドローダウン", "value": f"{drawdown:.1f}%", "inline": True},
        ]

        color = LEVEL_COLORS["success"] if daily_pnl >= 0 else LEVEL_COLORS["error"]

        payload = {
            "embeds": [
                {
                    "title": "日次レポート",
                    "color": color,
                    "fields": fields,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "footer": {"text": "Stella Trader | Daily Report"},
                }
            ]
        }
        return await self._post_webhook(payload)

    async def close(self) -> None:
        """セッションを閉じる。"""
        if self._session and not self._session.closed:
            await self._session.close()
            logger.info("Discord通知セッションを閉じました")
