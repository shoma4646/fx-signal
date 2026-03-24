# Stella Trader v2

仮想通貨自動売買システム。Python(ボットエンジン) + TypeScript(ダッシュボード予定)のハイブリッド構成。

## プロジェクト構成

```
stella-trader/
├── bot/                         # Pythonボットエンジン（メイン開発対象）
│   ├── pyproject.toml           # 依存管理（uv使用、hatchlingビルド）
│   ├── config.example.toml      # 設定ファイルテンプレート
│   ├── .env.example             # 環境変数テンプレート
│   ├── src/stella/
│   │   ├── __init__.py
│   │   ├── main.py              # CLIエントリーポイント（argparse）
│   │   ├── config.py            # pydantic-settings設定管理（.env + TOML）
│   │   ├── core/
│   │   │   ├── engine.py        # TradingEngine（asyncメインループ）
│   │   │   ├── portfolio.py     # PortfolioManager（ポジション統合管理、JSON永続化）
│   │   │   └── safety.py        # SafetyManager（多層安全機構）
│   │   ├── exchange/
│   │   │   ├── base.py          # BaseExchange抽象基底クラス
│   │   │   ├── bitbank.py       # BitbankExchange（ccxt経由、JPY建て、メイン）
│   │   │   └── bybit.py         # BybitExchange（ccxt経由、USDT建て、予備）
│   │   ├── strategies/
│   │   │   ├── base.py          # BaseStrategy + Signalデータクラス（buy/sell/hold）
│   │   │   └── trend.py         # TrendStrategy（EMAクロス + ADXフィルター + トレーリングストップ）
│   │   ├── indicators/
│   │   │   └── technical.py     # テクニカル指標（EMA, ADX, ATR, RSI, MACD, BB）
│   │   ├── notify/
│   │   │   └── discord.py       # Discord Webhook通知
│   │   ├── api/
│   │   │   └── server.py        # FastAPI REST API（ダッシュボード連携）
│   │   └── backtest/
│   │       └── runner.py        # バックテストエンジン（Trade + BacktestResult）
│   └── tests/
│       ├── conftest.py          # pytest共通フィクスチャ
│       ├── test_indicators.py   # テクニカル指標テスト
│       ├── test_portfolio.py    # ポートフォリオ管理テスト
│       ├── test_safety.py       # 安全機構テスト
│       └── test_strategies/
│           └── test_trend.py    # トレンド戦略テスト
├── v1/                          # 旧JavaScript実装（参考用、変更不要）
│   └── POSTMORTEM.md            # v1障害報告書（¥38,000損失の教訓）
├── docs/
│   └── design.md                # 設計書（アーキテクチャ、将来ロードマップ）
├── .tmp/
│   └── task.md                  # タスク管理（Phase 1進捗）
├── .github/
│   └── workflows/deploy.yml     # GitHub Actions（※v1向け、要更新）
├── .mise.toml                   # ツールバージョン管理（Python 3.12 + Node 20）
└── .gitignore
```

> **注意**: `dashboard/` ディレクトリはv2ではまだ未作成。v1のダッシュボードは `v1/dashboard/` に退避済み。Phase 2で新規構築予定。

## 技術スタック

### ボットエンジン（Python）
- **ランタイム**: Python 3.12+（mise経由）
- **パッケージ管理**: uv
- **取引所API**: ccxt >= 4.0.0
- **データ分析**: pandas >= 2.2.0, numpy >= 1.26.0
- **テクニカル指標**: pandas-ta >= 0.3.14b1
- **設定管理**: pydantic >= 2.5.0, pydantic-settings >= 2.1.0
- **API**: FastAPI >= 0.115.0, uvicorn >= 0.34.0
- **スケジューラ**: apscheduler >= 3.10.0
- **ログ**: structlog >= 24.1.0
- **通知**: aiohttp >= 3.9.0（Discord Webhook）

### 開発ツール
- **リンター/フォーマッター**: ruff >= 0.5.0
- **テスト**: pytest >= 8.0.0, pytest-asyncio >= 0.23.0, pytest-cov >= 4.1.0
- **バックテスト（optional）**: vectorbt >= 0.26.0, matplotlib >= 3.8.0

### 取引所
- **bitbank**（メイン）: JPY建て、Maker手数料-0.02%
- **Bybit**（予備）: USDT建て

## 環境変数

`.env.example` を `.env` にコピーして設定:

```bash
STELLA_EXCHANGE__API_KEY=your_api_key
STELLA_EXCHANGE__API_SECRET=your_api_secret
STELLA_NOTIFY__DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...
LOG_LEVEL=INFO
```

## 開発コマンド

### 起動モード

```bash
# ペーパートレード（推奨: まずこちらで検証）
cd bot && uv run python -m stella.main paper

# バックテスト
cd bot && uv run python -m stella.main backtest

# 本番（十分なテスト後のみ）
cd bot && uv run python -m stella.main live
```

### テスト

```bash
cd bot && uv run pytest                    # 全テスト実行
cd bot && uv run pytest -v                 # 詳細出力
cd bot && uv run pytest --cov=stella       # カバレッジ付き
cd bot && uv run pytest tests/test_safety.py  # 個別テスト
```

### リント

```bash
cd bot && uv run ruff check src/           # リントチェック
cd bot && uv run ruff format src/          # フォーマット
```

### 依存管理

```bash
cd bot && uv sync                          # 依存インストール
cd bot && uv add <package>                 # パッケージ追加
cd bot && uv sync --extra backtest         # バックテスト用依存も含む
```

## 開発ルール

### 戦略の追加方法

1. `strategies/base.py`の`BaseStrategy`を継承する
2. `analyze()`で`Signal`を返す（buy/sell/hold + メタデータ）
3. `get_position_size()`でリスクベースのポジションサイズを計算する
4. `backtest/runner.py`でバックテスト検証してから実運用に投入する

### 取引所の追加方法

1. `exchange/base.py`の`BaseExchange`を継承する
2. ccxt経由で `initialize()`, `fetch_ticker()`, `fetch_balance()`, `create_order()`, `cancel_order()`, `fetch_ohlcv()` を実装する
3. レート制限ハンドリングを含める
4. `config.py`のExchangeConfigに対応する設定を追加する

### 安全機構のルール（最重要）

- **注文前に必ず実残高をAPI経由で確認する**（v1の¥38,000損失の教訓 → `v1/POSTMORTEM.md`参照）
- ポジション管理は戦略レベルではなく`PortfolioManager`で統合管理する
- 安全チェックは`SafetyManager.can_trade()`を通す
- キルスイッチは即時全ポジション決済。手動resume必須
- 日次損失上限、最大ドローダウン、ATRベースの急変動検知が有効

### コーディング規約

- **非同期**: エンジン・取引所連携はすべて`async/await`パターン
- **ログ**: `structlog`を使用。`print()`は使わない
- **型**: pydanticモデルと型ヒントを必ず使用する
- **テスト**: 新機能には必ずユニットテストを追加する（`tests/`配下）
- **リント**: ruffでチェック・フォーマットしてからコミットする

## アーキテクチャ

### データフロー

```
取引所API → OHLCVデータ取得 → テクニカル指標計算 → 戦略分析（Signal生成）
  → SafetyManager.can_trade() → PortfolioManager（注文バリデーション）
  → 取引所API（注文実行） → Discord通知
```

### 主要クラス

| クラス | ファイル | 責務 |
|--------|----------|------|
| `TradingEngine` | `core/engine.py` | asyncメインループ、戦略登録・実行 |
| `PortfolioManager` | `core/portfolio.py` | ポジション統合管理、残高同期、PnL計算 |
| `SafetyManager` | `core/safety.py` | 多層安全機構、キルスイッチ |
| `BaseExchange` | `exchange/base.py` | 取引所抽象インターフェース |
| `BaseStrategy` | `strategies/base.py` | 戦略抽象基底クラス |
| `Signal` | `strategies/base.py` | 売買シグナル（buy/sell/hold + メタデータ） |
| `TrendStrategy` | `strategies/trend.py` | EMAクロス + ADXフィルター戦略 |
| `BacktestRunner` | `backtest/runner.py` | バックテスト実行、パフォーマンスレポート |

## 設計原則

- **バックテスト駆動**: すべての戦略はバックテストで検証してから実運用
- **状態と実残高の同期**: ポートフォリオ状態は実残高と定期同期し、乖離時はアラート+停止
- **取引所抽象化**: ccxt経由で将来の取引所追加が容易
- **リスクファースト**: 1トレードリスク上限、日次損失上限、最大ドローダウン監視
- **安全第一**: v1の障害を教訓に、すべての注文パスで残高検証を徹底

## 開発ステータス

### Phase 1（現在）: ボットエンジン基盤 ✅ ほぼ完了

- ✅ プロジェクトセットアップ、設定管理
- ✅ 取引所連携（bitbank + Bybit）
- ✅ テクニカル指標、トレンドフォロー戦略
- ✅ ポートフォリオ管理、安全機構
- ✅ エンジン、通知、バックテスト、API、CLI
- ⏳ 接続テスト用スクリプト
- ⏳ 統合テスト、CI/CD更新、デプロイ手順書

### Phase 2（予定）: ダッシュボード改善

- FastAPI連携によるリアルタイムデータ取得
- バックテスト結果表示
- Next.js v2ダッシュボード構築

## v1からの主な変更点

- JavaScript → Python（バックテスト基盤・指標ライブラリの豊富さ）
- BitFlyer → bitbank（Maker手数料-0.02%、板取引の流動性が国内トップクラス）
- 戦略ごとの個別ポジション管理 → ポートフォリオレベルの統合管理
- バックテスト基盤なし → vectorbt連携のバックテストパイプライン
- 固定値の安全閾値 → ATRベースの動的リスク管理
- 障害対応が事後 → 多層安全機構による事前防止
