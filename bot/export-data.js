#!/usr/bin/env node
/**
 * ダッシュボード用にデータをエクスポートする
 */

const fs = require('fs');
const path = require('path');
const bitflyer = require('./lib/bitflyer');

const DATA_DIR = path.join(__dirname, 'data');
const EXPORT_DIR = path.join(__dirname, '../dashboard/public/data');

// エクスポート先を作成
if (!fs.existsSync(EXPORT_DIR)) {
  fs.mkdirSync(EXPORT_DIR, { recursive: true });
}

// 現在の価格を取得（公開API）
async function getCurrentPrices() {
  const pairs = ['BTC_JPY', 'ETH_JPY', 'XRP_JPY', 'SOL_JPY'];
  const prices = {};
  
  for (const pair of pairs) {
    try {
      const ticker = await bitflyer.getTicker(pair);
      prices[pair.replace('_JPY', '')] = ticker.ltp;
    } catch (e) {
      // 取扱いのないペアはスキップ
    }
  }
  return prices;
}

// 残高を取得してJPY評価額を計算
async function getPortfolio() {
  try {
    const [balance, prices] = await Promise.all([
      bitflyer.getBalance(),
      getCurrentPrices()
    ]);
    
    let totalJPY = 0;
    const assets = [];
    
    for (const b of balance) {
      if (b.amount > 0) {
        let jpyValue = 0;
        
        if (b.currency_code === 'JPY') {
          jpyValue = b.amount;
        } else if (prices[b.currency_code]) {
          jpyValue = b.amount * prices[b.currency_code];
        }
        
        totalJPY += jpyValue;
        assets.push({
          currency: b.currency_code,
          amount: b.amount,
          available: b.available,
          jpyValue: Math.round(jpyValue)
        });
      }
    }
    
    return {
      totalJPY: Math.round(totalJPY),
      assets,
      prices,
      fetchedAt: new Date().toISOString()
    };
  } catch (error) {
    console.error('❌ Failed to fetch portfolio:', error.message);
    return null;
  }
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

// メイン実行（非同期）
async function main() {
  // portfolio.json を生成（API呼び出し）
  const portfolio = await getPortfolio();
  if (portfolio) {
    fs.writeFileSync(path.join(EXPORT_DIR, 'portfolio.json'), JSON.stringify(portfolio, null, 2));
    console.log(`✅ portfolio.json exported (Total: ¥${portfolio.totalJPY.toLocaleString()})`);
  }
  
  console.log('\n📊 Export complete!');
}

main().catch(console.error);
