# Stella Trader 本番運用 振り返り

## 発生日時
2026-02-05 20:13 〜 2026-02-06 06:41

## 被害
- JPY: ¥55,000 → ¥16,583 (約¥38,000減)
- ETH: 0.05 → 0.009 (大部分売却)
- 日次損失: ¥5,380

## 根本原因

### 1. 状態管理と実残高の分離
- `grid-state.json`のポジションはbotの「認識」
- 実際のbitFlyer残高は別物
- この2つが同期されていなかった

### 2. 既存資産の考慮不足
- 元々持っていたETH/BTCをbot管理対象として扱ってしまった
- 「botが買った分だけ売る」はずが、実際は残高全体に影響

### 3. 安全チェックの欠如
- 売り注文前: 実際に保有しているか確認なし
- 買い注文前: 十分なJPYがあるか確認なし

---

## 改善策

### 必須（再開前に実装）

#### A. 注文前の残高確認
```javascript
async executeBuy(level, currentPrice) {
  // 買い注文前にJPY残高確認
  const balance = await bitflyer.getBalance();
  const jpyAvailable = balance.find(b => b.currency_code === 'JPY').available;
  const required = this.settings.orderSize * currentPrice * 1.01; // 手数料込み
  
  if (jpyAvailable < required) {
    console.log(`[${this.name}] ⚠️ JPY不足 - スキップ`);
    return;
  }
  // ... 注文処理
}
```

#### B. 売り注文の制限
```javascript
async executeSell(level, currentPrice) {
  // 実際の保有量を確認
  const balance = await bitflyer.getBalance();
  const symbol = this.pair.replace('_JPY', '');
  const actualHolding = balance.find(b => b.currency_code === symbol)?.available || 0;
  
  // botが認識しているポジション vs 実際の保有量の小さい方
  const maxSellable = Math.min(this.state.position, actualHolding);
  const size = Math.min(this.settings.orderSize, maxSellable);
  
  if (size <= 0) {
    console.log(`[${this.name}] ⚠️ 売却可能数量なし - スキップ`);
    return;
  }
  // ... 注文処理
}
```

#### C. 初期ポジション設定
- 既存保有分をbotの管理対象外にする
- または、初期ポジションとして登録

#### D. 状態と残高の定期同期
- 1時間ごとに実残高とgrid-state.jsonを照合
- 不整合があればアラート

### 推奨（安全性向上）

#### E. 資金の分離
- bot用の資金上限を設定（例: ¥30,000まで）
- それ以上は使わない

#### F. より保守的な損切り
- 現在: -5%で損切り
- 提案: -3%で損切り、または損切り無効化

#### G. 1日の取引上限
- 取引回数の上限を設定
- 異常な回転を防ぐ

---

## 再開前チェックリスト

- [ ] A〜D の改善を実装
- [ ] DRY RUNで動作確認
- [ ] 初期残高を記録
- [ ] bot用資金を明確に分離
- [ ] 小額（¥10,000程度）でテスト運用

---

## 教訓

1. **DRY RUNと本番は別物** - 手数料、スリッページ、実残高の問題
2. **状態管理は実残高と同期すべき** - 仮想的なポジション管理は危険
3. **安全チェックは過剰なくらいで丁度いい** - 「多分大丈夫」は大丈夫じゃない
4. **段階的に規模を上げる** - いきなり全資金投入しない
