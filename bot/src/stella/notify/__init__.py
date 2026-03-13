"""notifyパッケージ - 通知モジュール群。

Discord Webhookを利用した通知機能を提供する。
"""

from stella.notify.discord import DiscordNotifier

__all__ = ["DiscordNotifier"]
