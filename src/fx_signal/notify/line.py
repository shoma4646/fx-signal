import httpx
import structlog

logger = structlog.get_logger()

LINE_NOTIFY_URL = "https://notify-api.line.me/api/notify"


def send(token: str, message: str) -> None:
    """LINE Notifyにメッセージを送信する。"""
    if not token:
        logger.warning("LINE_TOKEN未設定のため通知をスキップします")
        return

    with httpx.Client(timeout=10.0) as client:
        resp = client.post(
            LINE_NOTIFY_URL,
            headers={"Authorization": f"Bearer {token}"},
            data={"message": message},
        )

    if resp.status_code == 200:
        logger.info("LINE通知送信成功")
    else:
        logger.error("LINE通知送信失敗", status=resp.status_code, body=resp.text)
