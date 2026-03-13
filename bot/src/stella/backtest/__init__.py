"""バックテストモジュール。

過去データを使用したトレーディング戦略のバックテスト機能を提供する。
"""

from stella.backtest.runner import BacktestResult, BacktestRunner

__all__ = ["BacktestResult", "BacktestRunner"]
