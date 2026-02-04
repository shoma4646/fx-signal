#!/usr/bin/env node
require('dotenv').config();

const fs = require('fs');
const path = require('path');
const analysis = require('./lib/analysis');
const bitflyer = require('./lib/bitflyer');
const notify = require('./lib/notify');

const CONFIG_PATH = path.join(__dirname, 'config.json');
const STATE_PATH = path.join(__dirname, 'data/grid-state.json');
const TRADES_PATH = path.join(__dirname, 'data/trades.json');

async function dailyCheck() {
  console.log('═══════════════════════════════════════════');
  console.log('   🌟 Stella Daily Check');
  console.log('   ' + new Date().toLocaleString('ja-JP'));
  console.log('═══════════════════════════════════════════\n');

  const config = JSON.parse(fs.readFileSync(CONFIG_PATH, 'utf8'));
  let report = '📊 **Stella Trader 日次レポート**\n\n';

  // 残高取得
  try {
    const balances = await bitflyer.getBalance();
    report += '💰 **残高:**\n';
    for (const b of balances) {
      if (b.amount > 0 && ['JPY', 'BTC', 'ETH'].includes(b.currency_code)) {
        report += `  ${b.currency_code}: ${b.amount}\n`;
      }
    }
    report += '\n';
  } catch (e) {
    report += '💰 残高取得エラー\n\n';
  }

  // 戦略ごとの分析
  report += '📈 **戦略分析:**\n';
  
  const pairs = ['ETH_JPY', 'BTC_JPY'];
  for (const pair of pairs) {
    const symbol = pair.replace('_JPY', '');
    
    // 現在価格
    const ticker = await bitflyer.getTicker(pair);
    const currentPrice = ticker.ltp;
    
    // 移動平均
    const ma = await analysis.getMovingAverage(pair, 24);
    const deviation = ma ? (((currentPrice - ma) / ma) * 100).toFixed(1) : '?';
    
    // ボラティリティ
    const vol = await analysis.getVolatility(pair, 24);
    
    // 推奨グリッド幅
    const recommend = await analysis.recommendGridSpacing(pair);
    
    report += `\n**${symbol}:**\n`;
    report += `  現在: ¥${currentPrice.toLocaleString()}\n`;
    report += `  24h平均: ¥${ma ? ma.toLocaleString() : '?'} (${deviation}%)\n`;
    if (vol) {
      report += `  24hレンジ: ¥${vol.low.toLocaleString()} ~ ¥${vol.high.toLocaleString()} (${vol.rangePercent.toFixed(1)}%)\n`;
    }
    report += `  💡 推奨: ${recommend.reason}\n`;
  }

  // 取引履歴サマリー
  if (fs.existsSync(TRADES_PATH)) {
    const trades = JSON.parse(fs.readFileSync(TRADES_PATH, 'utf8'));
    const today = new Date().toISOString().split('T')[0];
    const todayTrades = trades.filter(t => t.timestamp.startsWith(today));
    
    const totalProfit = todayTrades
      .filter(t => t.profit !== null)
      .reduce((sum, t) => sum + t.profit, 0);
    
    report += `\n📝 **本日の取引:**\n`;
    report += `  取引回数: ${todayTrades.length}回\n`;
    report += `  損益: ¥${totalProfit.toLocaleString()}\n`;
  }

  // グリッド状態
  if (fs.existsSync(STATE_PATH)) {
    const state = JSON.parse(fs.readFileSync(STATE_PATH, 'utf8'));
    report += `\n📊 **ポジション:**\n`;
    
    for (const [name, s] of Object.entries(state)) {
      if (s.position > 0) {
        report += `  ${name}: ${s.position} (平均: ¥${s.avgBuyPrice?.toLocaleString() || '?'})\n`;
      }
    }
    
    const totalProfit = Object.values(state).reduce((sum, s) => sum + (s.totalProfit || 0), 0);
    report += `\n💰 **累計損益: ¥${totalProfit.toLocaleString()}**\n`;
  }

  report += '\n───────────────────────\n';
  report += '🤖 Stella が監視中です';

  console.log(report);
  
  // Discord に送信
  await notify.sendDiscord(report);
  
  console.log('\n✅ レポート送信完了');
}

dailyCheck().catch(console.error);
