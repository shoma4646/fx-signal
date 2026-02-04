# 🌟 Stella Trader

自動グリッドトレードBot + ダッシュボード

## 機能

### Bot
- 📊 グリッドトレード戦略（NORMAL / AGGRESSIVE）
- 🛡️ セーフティ機能（急変動検知、損切り、日次損失上限）
- 🔄 自動リバランス・自動再開
- 📱 Discord通知

### Dashboard
- 📈 損益推移グラフ
- 📊 取引統計
- 💼 ポジション表示
- 📋 取引履歴

## セットアップ

```bash
# 依存関係インストール
cd bot && npm install
cd ../dashboard && npm install

# Bot起動
cd bot && npm start

# ダッシュボード開発
cd dashboard && npm run dev
```

## 構成

```
stella-trader/
├── bot/                  # トレーディングBot
│   ├── bot.js           # メイン
│   ├── config.json      # 設定
│   ├── lib/             # ライブラリ
│   ├── strategies/      # 戦略
│   └── data/            # 取引データ
├── dashboard/           # Next.jsダッシュボード
│   ├── app/
│   └── public/data/     # エクスポートされたデータ
└── .github/workflows/   # 自動デプロイ
```

## ダッシュボード

GitHub Pages: https://shoma4646.github.io/stella-trader

3時間ごとに自動更新されます。

---

🤖 Powered by Stella
