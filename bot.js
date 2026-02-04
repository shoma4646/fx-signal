#!/usr/bin/env node
require('dotenv').config();

const fs = require('fs');
const path = require('path');
const GridStrategy = require('./strategies/grid');
const notify = require('./lib/notify');
const bitflyer = require('./lib/bitflyer');

const CONFIG_PATH = path.join(__dirname, 'config.json');

class CryptoBot {
  constructor() {
    this.config = null;
    this.strategies = {};
    this.running = false;
    this.configLastModified = null;
  }

  loadConfig() {
    const stats = fs.statSync(CONFIG_PATH);
    if (this.configLastModified && stats.mtimeMs === this.configLastModified) {
      return false; // 変更なし
    }

    this.config = JSON.parse(fs.readFileSync(CONFIG_PATH, 'utf8'));
    this.configLastModified = stats.mtimeMs;
    console.log(`\n📁 設定読込: v${this.config.version} (${this.config.updatedBy})`);
    console.log(`   Note: ${this.config.note}`);
    return true;
  }

  initStrategies() {
    this.strategies = {};
    
    for (const [pair, settings] of Object.entries(this.config.pairs)) {
      if (!settings.enabled) {
        console.log(`[${pair}] 無効 - スキップ`);
        continue;
      }

      if (settings.strategy === 'grid') {
        this.strategies[pair] = new GridStrategy(
          pair,
          settings.gridSettings,
          this.config.bot.dryRun
        );
        console.log(`[${pair}] グリッド戦略を初期化`);
      }
    }
  }

  async showStatus() {
    console.log('\n════════════════════════════════════');
    console.log('         🤖 Crypto Bot Status');
    console.log('════════════════════════════════════');
    
    // 残高表示
    try {
      const balances = await bitflyer.getBalance();
      console.log('\n💰 残高:');
      for (const b of balances) {
        if (b.amount > 0) {
          console.log(`   ${b.currency_code}: ${b.amount}`);
        }
      }
    } catch (e) {
      console.log('   残高取得エラー:', e.message);
    }

    // 戦略状態
    console.log('\n📊 戦略状態:');
    for (const [pair, strategy] of Object.entries(this.strategies)) {
      const stats = strategy.getStats();
      console.log(`   [${pair}]`);
      console.log(`      ポジション: ${stats.position}`);
      console.log(`      累計損益: ¥${stats.totalProfit.toLocaleString()}`);
      console.log(`      取引回数: ${stats.tradeCount}`);
    }

    console.log('\n════════════════════════════════════');
    console.log(`DRY RUN: ${this.config.bot.dryRun ? 'ON (実注文なし)' : 'OFF (本番モード)'}`);
    console.log(`チェック間隔: ${this.config.bot.checkIntervalSec}秒`);
    console.log('════════════════════════════════════\n');
  }

  async runOnce() {
    // 設定ファイルの変更をチェック
    if (this.loadConfig()) {
      this.initStrategies();
    }

    if (!this.config.bot.enabled) {
      console.log('Bot is disabled in config');
      return;
    }

    // 各戦略を実行
    for (const [pair, strategy] of Object.entries(this.strategies)) {
      await strategy.execute();
    }
  }

  async start() {
    console.log('🚀 Crypto Bot を起動します...\n');

    this.loadConfig();
    this.initStrategies();
    await this.showStatus();

    if (!this.config.bot.enabled) {
      console.log('⚠️ Bot is disabled. Enable it in config.json');
      return;
    }

    this.running = true;
    
    // メインループ
    while (this.running) {
      try {
        await this.runOnce();
      } catch (error) {
        console.error('Loop error:', error.message);
        await notify.notifyError(error.message);
      }

      // 次のチェックまで待機
      await this.sleep(this.config.bot.checkIntervalSec * 1000);
    }
  }

  stop() {
    console.log('\n🛑 Bot を停止します...');
    this.running = false;
  }

  sleep(ms) {
    return new Promise(resolve => setTimeout(resolve, ms));
  }
}

// メイン実行
const bot = new CryptoBot();

// Ctrl+C でグレースフルシャットダウン
process.on('SIGINT', () => {
  bot.stop();
  process.exit(0);
});

process.on('SIGTERM', () => {
  bot.stop();
  process.exit(0);
});

// 起動
bot.start().catch(err => {
  console.error('Fatal error:', err);
  process.exit(1);
});
