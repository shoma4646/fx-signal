"""テスト共通フィクスチャ。

全テストモジュールで共有されるフィクスチャを定義する。
OHLCVデータ、モック取引所、テスト用設定オブジェクトを提供する。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import numpy as np
import pandas as pd
import pytest


@pytest.fixture
def sample_ohlcv_df() -> pd.DataFrame:
    """トレンドパターンを含むリアルなOHLCVデータを生成する。

    前半50本は上昇トレンド、後半50本は下降トレンドを含む
    計100本のローソク足データを返す。
    """
    np.random.seed(42)
    n = 100

    # 基準価格: 前半は上昇、後半は下降
    base = np.concatenate([
        np.linspace(40000, 45000, 50),
        np.linspace(45000, 41000, 50),
    ])

    # ランダムなノイズを加える
    noise = np.random.normal(0, 100, n)
    close = base + noise

    # OHLCV各カラムを生成
    high = close + np.abs(np.random.normal(50, 30, n))
    low = close - np.abs(np.random.normal(50, 30, n))
    open_ = close + np.random.normal(0, 30, n)

    # 出来高: 平均1000、シグナル発生箇所では高出来高にする
    volume = np.random.uniform(500, 1500, n)
    # インデックス45-55付近(トレンド転換点)で出来高を増やす
    volume[43:57] = np.random.uniform(2000, 4000, 14)

    timestamps = pd.date_range("2026-01-01", periods=n, freq="1h")

    df = pd.DataFrame({
        "timestamp": timestamps,
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
        "symbol": "BTC/USDT",
    })

    return df


@pytest.fixture
def mock_exchange() -> MagicMock:
    """事前定義されたデータを返すモック取引所オブジェクトを生成する。

    fetch_balance、fetch_positions、create_market_orderの各メソッドを
    AsyncMockとして提供する。
    """
    exchange = MagicMock()

    exchange.fetch_balance = AsyncMock(return_value={
        "total": {"USDT": 10000.0},
        "free": {"USDT": 8000.0},
        "used": {"USDT": 2000.0},
    })

    exchange.fetch_positions = AsyncMock(return_value=[])

    exchange.create_market_order = AsyncMock(return_value={
        "id": "mock-order-001",
        "symbol": "BTC/USDT",
        "side": "buy",
        "amount": 0.01,
        "price": 42000.0,
        "average": 42000.0,
        "status": "closed",
    })

    exchange.fetch_ohlcv = AsyncMock(return_value=[])

    return exchange


@pytest.fixture
def sample_config() -> MagicMock:
    """テスト用の設定オブジェクトを生成する。"""
    config = MagicMock()
    config.exchange = {
        "api_key": "test-api-key",
        "api_secret": "test-api-secret",
        "testnet": True,
    }
    config.portfolio = {
        "max_positions": 3,
        "max_position_pct": 0.3,
    }
    config.safety = {
        "daily_loss_limit_pct": 0.05,
        "max_drawdown_pct": 0.20,
    }
    config.strategies = []
    config.trading_pairs = ["BTC/USDT", "ETH/USDT"]
    config.timeframe = "1h"
    config.ohlcv_limit = 100
    config.check_interval_sec = 60.0
    config.mode = "paper"
    config.discord_webhook_url = None
    return config
