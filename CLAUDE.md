# Stella Trader v2

仮想通貨自動売買システム。Python(ボットエンジン) + TypeScript(ダッシュボード)のハイブリッド構成。

## プロジェクト構成

```
stella-trader/
├── bot/                         # Pythonボットエンジン
│   ├── pyproject.toml           # 依存管理（uv使用）
│   ├── config.example.toml      # 設定ファイルテンプレート
│   ├── src/stella/
│   │   ├── main.py              # CLIエントリーポイント
│   │   ├── config.py            # pydantic設定管理
│   │   ├── core/
│   │   │   ├── engine.py        # トレーディングエンジン（メインループ）
│   │   │   ├── portfolio.py     # ポートフォリオ管理（ポジション統合管理）
│   │   │   └── safety.py        # 安全機構（キルスイッチ、損失制限）
│   │   ├── exchange/
│   │   │   ├── base.py          # 取引所抽象基底クラス
│   │   │   ├── bitbank.py       # bitbank実装（ccxt経由、メイン）
│   │   │   └── bybit.py         # Bybit実装（ccxt経由、予備）
│   │   ├── strategies/
│   │   │   ├── base.py          # 戦略基底クラス（Signal, BaseStrategy）
│   │   │   └── trend.py         # トレンドフォロー（EMAクロス + ADX）
│   │   ├── indicators/
│   │   │   └── technical.py     # テクニカル指標（pandas-ta）
│   │   ├── notify/
│   │   │   └── discord.py       # Discord通知
│   │   ├── api/
│   │   │   └── server.py        # FastAPI（ダッシュボード連携）
│   │   └── backtest/
│   │       └── runner.py        # バックテスト実行
│   └── tests/                   # pytest
├── dashboard/                   # Next.jsダッシュボード（Phase 2で改善予定）
├── v1/                          # 旧JavaScript実装（参考用）
├── docs/
│   └── design.md                # 設計書
└── .tmp/
    └── task.md                  # タスク管理
```

## 技術スタック

- **ボット**: Python 3.12+, ccxt, pandas-ta, pydantic, FastAPI, structlog
- **ダッシュボード**: Next.js 15, Recharts, Tailwind CSS
- **取引所**: bitbank（メイン、JPY建て）、Bybit（予備）
- **バージョン管理**: mise（Python 3.12 + Node 20）

## 開発ルール

### 戦略の追加方法

1. `strategies/base.py`の`BaseStrategy`を継承する
2. `analyze()`で`Signal`を返す（buy/sell/hold）
3. `get_position_size()`でリスクベースのポジションサイズを計算する
4. `backtest/runner.py`でバックテスト検証してから実運用に投入する

### 安全機構のルール

- **注文前に必ず実残高をAPI経由で確認する**（v1の¥38,000損失の教訓）
- ポジション管理は戦略レベルではなく`PortfolioManager`で統合管理する
- 安全チェックは`SafetyManager.can_trade()`を通す
- キルスイッチは即時全ポジション決済。手動resume必須

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
cd bot && uv run pytest
```

## 設計原則

- **バックテスト駆動**: すべての戦略はバックテストで検証してから実運用
- **状態と実残高の同期**: ポートフォリオ状態は実残高と定期同期し、乖離時はアラート+停止
- **取引所抽象化**: ccxt経由で将来の取引所追加が容易
- **リスクファースト**: 1トレードリスク上限、日次損失上限、最大ドローダウン監視

## v1からの主な変更点

- JavaScript → Python（バックテスト基盤・指標ライブラリの豊富さ）
- BitFlyer → bitbank（Maker手数料-0.02%、板取引の流動性が国内トップクラス）
- 戦略ごとの個別ポジション管理 → ポートフォリオレベルの統合管理
- バックテスト基盤なし → vectorbt連携のバックテストパイプライン
- 固定値の安全閾値 → ATRベースの動的リスク管理
