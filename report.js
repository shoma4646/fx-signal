#!/usr/bin/env node
require('dotenv').config();

const fs = require('fs');
const path = require('path');
const bitflyer = require('./lib/bitflyer');

const CONFIG_PATH = path.join(__dirname, 'config.json');
const STATE_PATH = path.join(__dirname, 'data/grid-state.json');
const TRADES_PATH = path.join(__dirname, 'data/trades.json');

async function generateReport() {
  console.log('═══════════════════════════════════════════');
  console.log('        📊 Crypto Bot レポート');
  console.log('        ' + new Date().toLocaleString('ja-JP'));
  console.log('═══════════════════════════════════════════\n');

  // 設定読込
  const config = JSON.parse(fs.readFileSync(CONFIG_PATH, 'utf8'));
  console.log(`📁 設定: v${config.version} by ${config.updatedBy}`);
  console.log(`   DRY RUN: ${config.bot.dryRun ? 'ON' : 'OFF'}`);
  console.log('');

  // 残高
  console.log('💰 現在の残高:');
  try {
    const balances = await bitflyer.getBalance();
    for (const b of balances) {
      if (b.amount > 0) {
        console.log(`   ${b.currency_code.padEnd(5)}: ${b.amount}`);
      }
    }
  } catch (e) {
    console.log('   エラー:', e.message);
  }
  console.log('');

  // 現在価格
  console.log('📈 現在価格:');
  for (const pair of Object.keys(config.pairs)) {
    try {
      const ticker = await bitflyer.getTicker(pair);
      const symbol = pair.replace('_JPY', '');
      console.log(`   ${symbol.padEnd(5)}: ¥${ticker.ltp.toLocaleString()}`);
    } catch (e) {
      // skip
    }
  }
  console.log('');

  // 戦略状態
  if (fs.existsSync(STATE_PATH)) {
    const state = JSON.parse(fs.readFileSync(STATE_PATH, 'utf8'));
    console.log('📊 戦略状態:');
    
    for (const [pair, s] of Object.entries(state)) {
      const symbol = pair.replace('_JPY', '');
      console.log(`\n   【${symbol}】`);
      console.log(`   ポジション: ${s.position}`);
      if (s.position > 0) {
        console.log(`   平均取得価格: ¥${s.avgBuyPrice.toLocaleString()}`);
      }
      console.log(`   累計損益: ¥${s.totalProfit.toLocaleString()}`);
      console.log(`   取引回数: ${s.tradeCount}`);
      console.log(`   最終更新: ${s.lastUpdate}`);
    }
  }
  console.log('');

  // 直近の取引
  if (fs.existsSync(TRADES_PATH)) {
    const trades = JSON.parse(fs.readFileSync(TRADES_PATH, 'utf8'));
    const recent = trades.slice(-5);
    
    if (recent.length > 0) {
      console.log('📝 直近の取引:');
      for (const t of recent) {
        const emoji = t.side === 'BUY' ? '🟢' : '🔴';
        const symbol = t.pair.replace('_JPY', '');
        let line = `   ${emoji} ${symbol} ${t.side} ${t.size} @ ¥${t.price.toLocaleString()}`;
        if (t.profit !== null) {
          line += ` (損益: ¥${t.profit.toLocaleString()})`;
        }
        console.log(line);
      }
    }
  }

  console.log('\n═══════════════════════════════════════════\n');

  // ステラへの提案
  console.log('💡 ステラへの情報:');
  console.log('   config.json を編集してパラメータ調整可能');
  console.log('   - gridSpacingPercent: グリッド間隔');
  console.log('   - takeProfitPercent: 利確ライン');
  console.log('   - orderSize: 1回の注文量');
  console.log('   - dryRun: false で本番モード');
  console.log('');
}

generateReport().catch(console.error);
