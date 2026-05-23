import httpx
import structlog

logger = structlog.get_logger()

PUSH_URL = "https://api.line.me/v2/bot/message/push"


def send(token: str, user_id: str, message: str) -> None:
    """LINE Messaging API（Push Message）でメッセージを送信する。"""
    if not token or not user_id:
        logger.warning("LINE_TOKEN / LINE_USER_ID 未設定のため通知をスキップします")
        return

    with httpx.Client(timeout=10.0) as client:
        resp = client.post(
            PUSH_URL,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json={
                "to": user_id,
                "messages": [{"type": "text", "text": message}],
            },
        )

    if resp.status_code == 200:
        logger.info("LINE通知送信成功")
    else:
        logger.error("LINE通知送信失敗", status=resp.status_code, body=resp.text)
