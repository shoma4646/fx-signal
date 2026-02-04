#!/usr/bin/env node
/**
 * ダッシュボード用にデータをエクスポートする
 */

const fs = require('fs');
const path = require('path');

const DATA_DIR = path.join(__dirname, 'data');
const EXPORT_DIR = path.join(__dirname, '../dashboard/public/data');

// エクスポート先を作成
if (!fs.existsSync(EXPORT_DIR)) {
  fs.mkdirSync(EXPORT_DIR, { recursive: true });
}

// trades.json をコピー
const tradesPath = path.join(DATA_DIR, 'trades.json');
if (fs.existsSync(tradesPath)) {
  fs.copyFileSync(tradesPath, path.join(EXPORT_DIR, 'trades.json'));
  console.log('✅ trades.json exported');
}

// grid-state.json から stats を生成
const statePath = path.join(DATA_DIR, 'grid-state.json');
if (fs.existsSync(statePath)) {
  const state = JSON.parse(fs.readFileSync(statePath, 'utf8'));
  
  let totalProfit = 0;
  let tradeCount = 0;
  const positions = [];
  
  for (const [name, data] of Object.entries(state)) {
    totalProfit += data.totalProfit || 0;
    tradeCount += data.tradeCount || 0;
    
    if (data.position > 0) {
      positions.push({
        name,
        position: data.position,
        avgBuyPrice: data.avgBuyPrice,
        totalProfit: data.totalProfit
      });
    }
  }
  
  // trades.json から勝敗をカウント
  let winCount = 0;
  let lossCount = 0;
  
  if (fs.existsSync(tradesPath)) {
    const trades = JSON.parse(fs.readFileSync(tradesPath, 'utf8'));
    for (const trade of trades) {
      if (trade.profit !== null) {
        if (trade.profit >= 0) winCount++;
        else lossCount++;
      }
    }
  }
  
  const stats = {
    totalProfit,
    tradeCount,
    winCount,
    lossCount,
    positions,
    lastUpdate: new Date().toISOString()
  };
  
  fs.writeFileSync(path.join(EXPORT_DIR, 'stats.json'), JSON.stringify(stats, null, 2));
  console.log('✅ stats.json exported');
}

// safety-state.json をコピー
const safetyPath = path.join(DATA_DIR, 'safety-state.json');
if (fs.existsSync(safetyPath)) {
  fs.copyFileSync(safetyPath, path.join(EXPORT_DIR, 'safety.json'));
  console.log('✅ safety.json exported');
}

console.log('\n📊 Export complete!');
