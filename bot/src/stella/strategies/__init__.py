"""strategiesパッケージ - トレーディング戦略モジュール群。

各種トレーディング戦略の基底クラスと具体的な戦略実装を提供する。
"""

from stella.strategies.base import BaseStrategy, Signal
from stella.strategies.trend import TrendStrategy

__all__ = ["BaseStrategy", "Signal", "TrendStrategy"]
