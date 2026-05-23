import subprocess

import structlog

logger = structlog.get_logger()


def send(title: str, message: str, sound: bool = True) -> None:
    """macOSのデスクトップ通知を送信する。"""
    sound_clause = 'sound name "Glass"' if sound else ""
    script = f'display notification "{message}" with title "{title}" {sound_clause}'.strip()

    result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)

    if result.returncode == 0:
        logger.info("Mac通知送信成功", title=title)
    else:
        logger.error("Mac通知送信失敗", stderr=result.stderr)
