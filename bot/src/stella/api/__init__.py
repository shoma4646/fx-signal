"""APIサーバーモジュール。

FastAPIベースのダッシュボード用REST APIを提供する。
"""

from stella.api.server import create_app

__all__ = ["create_app"]
