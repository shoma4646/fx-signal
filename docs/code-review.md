# Stella Trader v2 コードレビュー

**初回レビュー**: 2026-03-16
**再レビュー**: 2026-03-24
**対象**: bot/src/stella/ 全モジュール

---

## 総合評価

アーキテクチャ設計は堅実で、v1の教訓（¥38,000損失）を踏まえた安全機構の設計思想は適切。
しかし、**実装レベルでは未接続・未呼出のコードが複数あり、設計意図が実現されていない箇所がある**。
前回レビュー（3/16）から**コード変更は0件**であり、指摘事項は全て未修正のまま残っている。

本再レビューでは、前回の指摘に加えて**戦略ロジックの有効性**・**モジュール間データフローの断絶**・
**バックテスト信頼性**に焦点を当てて深掘りする。

---

## A. 致命的なデータフロー断絶（前回指摘 + 深掘り）

前回CRITICALとした5件は全て未修正。ここではそれらが**連鎖して引き起こす問題**を整理する。

### A.1 エグジットが完全に機能しない — 全フローが壊れている

**根本原因**: `trend.py:295`

```python
position = portfolio.get_position(symbol) if hasattr(portfolio, "get_position") else None
```

`PortfolioManager` に `get_position(symbol)` は存在しない。`hasattr` で回避しているため
例外は出ないが、**常に `None` が返る**。これにより:

1. `_check_exit_conditions()` (L298) — **呼ばれない** → ストップロス/トレーリングストップが不動作
2. デッドクロスの売り (L365) — `position is not None` が常にFalse → **売りシグナルが出ない**
3. エントリー後、**決済する手段がない** → ポジションが無限に保持される

**影響**: 買いシグナルで入ったポジションが決済されず、損失が拡大し続ける。
v1の¥38,000損失と同じパターンの再発リスク。

**修正案**: `PortfolioManager` に `get_position(symbol)` を追加する:

```python
def get_position(self, symbol: str) -> Position | None:
    for p in self._positions.values():
        if p.symbol == symbol and p.is_open:
            return p
    return None
```

### A.2 ATRがエンジン→戦略に正しく渡らない

`engine.py:293`:
```python
atr = getattr(self._portfolio, "get_current_atr", lambda s: 0.0)(signal.symbol)
```

`PortfolioManager` に `get_current_atr()` は存在しない。常に `0.0` → フォールバック `1.0` が使われる。

BTC/JPY の ATR は数万〜数十万円。`atr=1.0` でポジションサイズを計算すると:
```
position_size = (balance × 0.02) / (1.0 × 2.0) = balance × 0.01
```
残高100万円なら1万円分 — 本来の100分の1以下の極小ポジションになる。

**影響**: シグナルが出てもポジションが小さすぎて実質的に取引しないのと同じ。

### A.3 `validate_order()` vs `validate_signal()` — 使われ方の不一致

`PortfolioManager` には `validate_order(symbol, side, quantity, price)` が定義されている (L377)。
しかし `engine.py:277` は `validate_signal(signal)` を呼ぼうとしている — 存在しないメソッド。
`hasattr` チェックで回避されるため、**注文前バリデーションがスキップされる**。

v1の教訓「注文前に必ず実残高をAPI経由で確認」が実現されていない。

### A.4 ポートフォリオ更新パス `record_trade()` が未定義

`engine.py:325-333`:
```python
if hasattr(self._portfolio, "record_trade"):
    self._portfolio.record_trade(...)
```

`PortfolioManager` に `record_trade()` は存在しない。`open_position()` と `close_position()` はあるが
呼ばれていない。**注文が成功してもポートフォリオ状態が更新されない**。

---

## B. 戦略ロジックの評価（深掘り）

### B.1 エントリーロジック — EMA(9/21)クロス + ADX + 出来高

**設計の妥当性**: 教科書的なトレンドフォロー。ロジック自体は標準的。

**問題1: 遅延シグナル**

EMA(9/21)のクロスは**トレンドが既に進行してから**検出される。
1時間足の場合、ゴールデンクロス発生時にはトレンドの初動から数時間〜十数時間が経過済み。

```
実際の安値 → ... → EMA(9)がEMA(21)を上抜け → ここでエントリー
              ↑ この区間の利益を取り逃す
```

**改善案**: MACD ヒストグラムの傾き変化（モメンタム先行指標）を補助的に使い、
クロス「直前」のシグナルを検出する。あるいは EMA(5/13) に短縮。

**問題2: 出来高フィルターが1本のバーのみ**

```python
# trend.py:331
if current_volume < avg_volume * self.volume_mult:
```

クロスオーバーが発生した**その1本だけ**の出来高を見ている。
大口の偶発的約定でスパイクが発生した場合にフィルターを通過してしまう。

**改善案**: 直近3〜5本の出来高が平均以上かを確認する。

**問題3: ADX独自実装の精度**

`trend.py:141-142` — Wilder's smoothing を `ewm(span=period)` で代用:
```python
atr = pd.Series(tr, index=df.index).ewm(span=period, adjust=False).mean()
```

Wilder's smoothing は `alpha = 1/period` だが、`ewm(span=period)` は `alpha = 2/(period+1)`。
**ADX値が標準実装と異なる**。閾値25の意味が変わる。

`indicators/technical.py` に pandas-ta 経由の `calculate_adx()` があるのに未使用。

### B.2 エグジットロジック — 3重のエグジット（全て不動作）

| エグジット | 条件 | 状態 |
|-----------|------|------|
| ストップロス | `current_close <= stop_loss` | `position` が常に `None` → **不動作** |
| トレーリングストップ | 利益 >= ATR×3 かつ最高値から ATR×1.5 下落 | 同上 → **不動作** |
| デッドクロス | EMA(9) < EMA(21) | `position is not None` 条件 → **不動作** |

**全エグジットが機能しない**。`get_position()` の修正が最優先。

仮に修正後の評価:

**トレーリングストップの発動条件が厳しすぎる:**

```python
trailing_trigger = current_atr * self.trailing_atr_mult  # ATR × 3.0
```

BTC/JPY の 1h ATR が 50,000円の場合、利益が 150,000円 (≈ 1.5%) にならないと発動しない。
さらに `highest > current_close` の条件で最高値からの下落がないと判定しない。
実質的にほぼ発動しない。

**改善案**: `trailing_atr_mult` を `1.5〜2.0` に緩和。

**テイクプロフィットが未設定:**

```python
take_profit=None,  # 常にNone
```

利確ターゲットがないため、含み益がデッドクロスまで返り続ける。
R:R 比（リスク対リワード）の概念が欠落。

**改善案**: `take_profit = current_close + current_atr * 4.0` のように設定。

### B.3 ポジションサイジング

```python
risk_amount = balance * self.risk_pct          # 残高 × 2%
stop_distance = atr * self.stop_loss_atr_mult  # ATR × 2.0
position_size = risk_amount / stop_distance
```

**設計自体は正しい**（固定リスク率方式）。ただし:

1. **ATRが正しく渡らない** (A.2参照) → サイズが不適切
2. **`strength` が無視される** → 高確度シグナルも低確度シグナルも同じサイズ
3. **最小取引単位・価格精度の考慮なし** → bitbank の最小注文数量を下回る可能性

### B.4 シグナル強度の問題

```python
strength = min(1.0, (current_adx / 50.0) * 0.6 + min(ema_spread * 100, 1.0) * 0.4)
```

- 重み `0.6` / `0.4` に根拠なし
- `ema_spread * 100` — EMA乖離率0.01(1%)で最大値。BTC/JPYの1h足では常にこの閾値以下
- **strengthが使われていない** — ポジションサイジングにもフィルタリングにも反映されない

### B.5 レンジ相場での脆弱性

ADX > 25 のフィルターがあるが:

1. ADX の独自実装が標準と異なる（B.1 問題3）
2. ADX は**遅延指標** — レンジ突入後も高い値を維持する時間がある
3. **ADX < 20 でのエントリー完全停止**の仕組みがない

レンジ相場で EMA クロスが頻発 → 往復ビンタ（ウィップソー）のリスクが高い。

**改善案**:
- ボリンジャーバンド幅（BBW）でレンジ/トレンドを判定
- ADX が20未満の期間は取引を完全停止
- クールダウンを現在の60分より長く設定（120分以上）

---

## C. バックテスト信頼性の問題

### C.1 手数料の二重計上（再確認）

エントリー時:
```python
commission = execution_price * quantity * self._commission_rate  # L291
balance -= commission
```

決済時のTradeレコード:
```python
commission=commission + position_entry_price * position_quantity * self._commission_rate  # L328-331
```

決済時の `commission` は決済手数料のみのはずが、`+ エントリーコスト` が加算されている。
**Tradeの `commission` フィールドが実際の2倍になる**。PnLの `pnl_net` 自体は正しい（L315で
決済手数料のみ引かれる）が、レポートの手数料合計が過大。

### C.2 エントリーコストが残高から引かれない

```python
# L291-292: 手数料のみ引かれるが、ポジション取得コスト自体は引かれない
balance -= commission  # 手数料だけ
# balance -= order_cost  ← これがない
```

残高が減らないため、**連続でエントリーし続けることが可能**。
1つのポジションしか持てないはずが、`position_side` で制御されているのでロジックは壊れない。
ただし `balance` が正確でなくなり、エクイティカーブが不正確。

### C.3 ポートフォリオオブジェクトの不整合

バックテストで `PortfolioManager` を作成するが (L221)、**実際には使われない**。
バックテストは独自の `position_side` / `position_entry_price` 変数で管理し、
`portfolio.open_position()` / `close_position()` を呼ばない。

つまり `strategy.analyze(historical_slice, portfolio)` に渡す `portfolio` は
**常に空のポートフォリオ**。戦略がポートフォリオを参照してエグジット判定する場合
（本来の設計意図）、バックテストでは常にポジションなしと判定される。

**影響**: バックテストのエグジットシグナルが正しくない。
エントリーのみ機能し、エグジットは `position_side` を見る独自ロジックに依存。

### C.4 シャープレシオの意味が不正確

```python
annualization_factor: float = 365.0  # L530
```

- 1hバーの場合、1日に24データポイント → リターンは「時間足リターン」
- `sqrt(365)` で年率化するのは**日次リターン前提**
- 正しくは `sqrt(24 * 365)` = `sqrt(8760)` ≈ 93.6 が必要

現在のシャープレシオは **√24 ≈ 4.9倍 過小評価** されている。

---

## D. モジュール間インターフェースの整合性マトリクス

戦略・エンジン・ポートフォリオ間で想定するメソッドが一致しているかの検証:

| 呼び出し元 | 呼び出すメソッド | 実在 | 状態 |
|-----------|-----------------|------|------|
| `trend.py:295` | `portfolio.get_position(symbol)` | **なし** | CRITICAL |
| `engine.py:277` | `portfolio.validate_signal(signal)` | **なし** | hasattrで回避 |
| `engine.py:293` | `portfolio.get_current_atr(symbol)` | **なし** | フォールバック1.0 |
| `engine.py:325` | `portfolio.record_trade(...)` | **なし** | hasattrで回避 |
| `engine.py:442` | `safety.check_safety(portfolio)` | **なし** | hasattrで回避 |
| `engine.py:437` | `safety.is_kill_switch_active()` | **なし** | hasattrで回避 |
| `engine.py:456` | `portfolio.get_open_positions()` | **なし** | hasattrで回避 |

**7箇所のインターフェース不一致**。`hasattr` による防御的プログラミングが
例外を防いでいるが、機能は全て無効化されている。

**根本原因**: 各モジュールが独立に開発され、結合テストが行われていない。

---

## E. テストカバレッジ（変更なし）

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

テスト済みモジュールの品質は高い。
ただし**モジュール間結合を検証するテストが皆無**なため、
上記セクションDの7箇所の不一致が検出されていない。

---

## F. 改善の優先順位（再整理）

### Phase 0: 動作可能にする（現状は動かない）

| # | タスク | 影響 |
|---|--------|------|
| 1 | `PortfolioManager.get_position(symbol)` 追加 | エグジット復活 |
| 2 | `engine.py` のポートフォリオ更新パスを `open_position()`/`close_position()` に修正 | 状態追跡 |
| 3 | `engine.py` の `validate_signal` → `validate_order` に修正 | 注文前検証 |
| 4 | `engine.py` の ATR 取得を OHLCV データから直接計算に変更 | サイズ適正化 |
| 5 | ポートフォリオの通貨判定を取引所に応じて JPY/USDT 切替 | 残高同期 |

### Phase 1: 安全に稼働させる

| # | タスク | 影響 |
|---|--------|------|
| 6 | `save_state()`/`load_state()` を `shutdown()`/`initialize()` で呼出 | 状態永続化 |
| 7 | Discord webhook パス修正 | 通知復活 |
| 8 | `can_trade()` にボラティリティチェック追加 | 安全性 |
| 9 | 日次リセットスケジューラ実装 | 日次制限 |
| 10 | ペーパートレードの残高減算 | 検証精度 |

### Phase 2: 戦略の有効性を改善

| # | タスク | 影響 |
|---|--------|------|
| 11 | テイクプロフィット追加（ATR × 4.0） | 利確 |
| 12 | トレーリングストップ閾値緩和（ATR×3→1.5） | エグジット改善 |
| 13 | ADX計算を pandas-ta に統一 | 指標精度 |
| 14 | レンジ相場フィルター追加（BBW or ADX<20停止） | ウィップソー防止 |
| 15 | strength をポジションサイズに反映 | リスク調整 |

### Phase 3: バックテスト信頼性

| # | タスク | 影響 |
|---|--------|------|
| 16 | バックテスト内でポートフォリオを正しく更新 | エグジット検証 |
| 17 | 手数料二重計上の修正 | 指標精度 |
| 18 | エントリーコストの残高反映 | エクイティ正確性 |
| 19 | シャープレシオの時間足対応 | 指標精度 |
| 20 | 統合テスト追加（エンジン×戦略×ポートフォリオ） | 回帰防止 |
