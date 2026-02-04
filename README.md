# 🌟 Stella Trader

bitFlyerでグリッドトレードを行う自動売買Bot。ステラ（AI）がパラメータ調整をサポート。

## 機能

- 📊 グリッドトレード戦略
- 🔄 複数通貨ペア対応（ETH_JPY, BTC_JPY）
- 📱 Discord通知（Webhook）
- 🤖 AI（ステラ）によるパラメータ最適化
- 🧪 DRY RUNモード（シミュレーション）

## セットアップ

```bash
npm install
cp .env.example .env
# .env にAPIキーを設定
```

## 使い方

```bash
# Bot起動
npm start

# レポート表示
npm run report

# 残高確認
npm run balance
```

## 設定

`config.json` でパラメータを調整：

```json
{
  "bot": {
    "enabled": true,
    "dryRun": true,        // trueでシミュレーション
    "checkIntervalSec": 30
  },
  "pairs": {
    "ETH_JPY": {
      "enabled": true,
      "gridSettings": {
        "gridSpacingPercent": 1.5,  // グリッド間隔
        "takeProfitPercent": 1.5,   // 利確ライン
        "orderSize": 0.01           // 注文サイズ
      }
    }
  }
}
```

## ファイル構成

```
stella-trader/
├── bot.js           # メインBot
├── report.js        # レポート生成
├── config.json      # 設定ファイル
├── lib/
│   ├── bitflyer.js  # bitFlyer API
│   └── notify.js    # 通知
├── strategies/
│   └── grid.js      # グリッド戦略
└── data/            # ログ・状態保存
```

## ライセンス

MIT

---

🤖 Powered by Stella
