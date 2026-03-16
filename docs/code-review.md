# Stella Trader v2 コードレビュー

**レビュー日**: 2026-03-16
**対象**: bot/src/stella/ 全モジュール

---

## 総合評価

アーキテクチャ設計は堅実で、v1の教訓（¥38,000損失）を踏まえた安全機構の設計思想は適切。
しかし、**実装レベルでは未接続・未呼出のコードが複数あり、設計意図が実現されていない箇所がある**。
本番稼働前に修正が必須な問題が複数存在する。

---

## 1. CRITICAL（本番稼働不可）

### 1.1 ポートフォリオの通貨不一致 — `portfolio.py`

`sync_with_exchange()` が USDT 残高を参照しているが、メイン取引所の bitbank は JPY 建て。
bitbank 接続時に残高が常に `0.0` になり、全注文がバリデーションで拒否される。

```python
# portfolio.py:130-134 — 現状
usdt_info = balances.get("USDT", {})
exchange_balance = float(usdt_info.get("total", 0.0) ...)

# 修正案: 取引所設定から通貨を判定
currency = "JPY" if exchange_name == "bitbank" else "USDT"
currency_info = balances.get(currency, {})
```

### 1.2 安全チェックの欠落 — `safety.py`

`check_volatility()` が定義されているが、`can_trade()` から呼ばれていない。
高ボラティリティ時の自動停止が機能しない。

```python
# safety.py — can_trade() 内で check_volatility() が未呼出
# 修正: can_trade() のチェーンに追加する
```

### 1.3 存在しないメソッドの呼び出し — `trend.py:295`

```python
position = portfolio.get_position(symbol)  # get_position() は未定義
```

`PortfolioManager` には `get_position(symbol)` メソッドが存在しない。
ポジション保有時のエグジット判定で必ずクラッシュする。
`get_positions()` で取得してシンボルでフィルタする必要がある。

### 1.4 ペーパートレードの残高未更新 — `bitbank.py`, `bybit.py`

ペーパーモードで注文を実行しても `_paper_balance` が減算されない。
無限に買い注文が通り、リスク管理の検証ができない。

### 1.5 ポートフォリオ状態の永続化が未接続 — `engine.py`

`save_state()` / `load_state()` が実装済みだが、`engine.py` から一度も呼ばれない。
ボット再起動時に全ポジション情報が消失する。

---

## 2. HIGH（機能不全）

### 2.1 Discord通知が常に無効 — `engine.py:161`

```python
# 現状: Config直下を参照（存在しない）
discord_webhook = getattr(self._config, "discord_webhook_url", None)

# 正しくは:
discord_webhook = self._config.notify.discord_webhook_url
```

### 2.2 日次リセットが未実行 — `engine.py`, `safety.py`

`reset_daily()` が定義されているが、どこからも呼ばれていない。
日次損失リミット・連続損失カウンタが日を跨いでもリセットされず、
一度リミットに達すると永続的に取引停止になる。

### 2.3 ボラティリティ自動再開のバグ — `safety.py:106-108`

`_volatility_paused_at` が `resume()` 後にクリアされないため、
自動再開が初回しか機能しない。

### 2.4 API認証なし — `api/server.py`

全エンドポイントが認証なしで公開。キルスイッチを含む全操作が
ネットワークアクセス可能な誰でも実行可能。

```python
# CORS も全開放
allow_origins=["*"]
```

### 2.5 EMAクロスオーバー初回検出不可 — `trend.py:207-226`

前回値がない初回実行時は常に `None` を返す。
ボット起動後の最初のシグナルを見逃す。

---

## 3. MEDIUM（精度・堅牢性）

### 3.1 バックテストの手数料二重計上 — `backtest/runner.py:328-330`

エントリー手数料がトレード記録に二重で加算される。
バックテスト結果が実際より悪く見える。

### 3.2 ADXの重複実装 — `trend.py:115-158`

`TechnicalIndicators.calculate_adx()` が存在するにもかかわらず、
トレンド戦略内で独自にADXを再実装。ゼロ除算リスクあり。

### 3.3 エクスポージャー計算が旧価格基準 — `portfolio.py:375`

```python
# 現在価格ではなくエントリー価格で計算 → 真のリスクを反映しない
return sum(p.entry_price * p.quantity for p in self._positions.values())
```

### 3.4 バックテストのループ範囲 — `backtest/runner.py:239`

```python
for i in range(min_bars, len(df) - 1):  # 最終バーが未処理
```

### 3.5 シャープレシオの年換算 — `backtest/runner.py:530`

365日で年換算しているが、取引日ベースなら252日が標準。
シャープレシオが過大評価される。

---

## 4. テストカバレッジ

| モジュール | テスト数 | 評価 |
|-----------|---------|------|
| indicators/technical.py | 57 | ✅ 良好 |
| core/portfolio.py | 47 | ✅ 良好 |
| core/safety.py | 59 | ✅ 良好 |
| strategies/trend.py | 63 | ✅ 良好 |
| core/engine.py | 0 | ❌ 未テスト |
| exchange/*.py | 0 | ❌ 未テスト |
| backtest/runner.py | 0 | ❌ 未テスト |
| api/server.py | 0 | ❌ 未テスト |
| config.py | 0 | ❌ 未テスト |
| notify/discord.py | 0 | ❌ 未テスト |

テスト済みモジュールの品質は高い（境界値テスト、非同期テスト、日本語ドキュメント付き）。
しかし**コアエンジン・取引所層・バックテストが未テスト**であり、
統合テスト・E2Eテストも存在しない。

---

## 5. 設計面の良い点

- **モジュラー設計**: 取引所・戦略・通知が疎結合で拡張しやすい
- **型安全**: pydantic + dataclass + type hints の一貫した使用
- **非同期I/O**: ccxt・Discord通知がasync/awaitで適切に非同期化
- **不変性**: `Signal(frozen=True)` でシグナルの意図しない変更を防止
- **リスクファースト**: ATRベースのポジションサイジング・動的ストップロスの設計思想

---

## 6. 推奨アクションプラン

### Phase 1: 本番稼働前の必須修正
1. ポートフォリオの通貨判定ロジック修正
2. `can_trade()` にボラティリティチェック追加
3. `get_position()` メソッド追加 or 呼び出し修正
4. ペーパートレードの残高減算実装
5. エンジンに `save_state()` / `load_state()` 接続
6. Discord webhook の設定パス修正

### Phase 2: 安定運用に向けた改善
7. 日次リセットスケジューラ実装
8. API認証（JWT or API Key）追加
9. バックテストの手数料・ループ修正
10. エンジン・取引所の統合テスト追加

### Phase 3: 品質向上
11. E2Eペーパートレードテスト
12. ADX実装の統一（pandas-ta利用）
13. エクスポージャーの時価評価
14. シャープレシオ計算の修正
