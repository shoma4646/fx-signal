#!/usr/bin/env node
/**
 * テクニカル指標テスト
 * 
 * Usage: node test-indicators.js [pair]
 * Example: node test-indicators.js ETH_JPY
 */
require('dotenv').config();

const indicators = require('./lib/indicators');
const analysis = require('./lib/analysis');

const pair = process.argv[2] || 'ETH_JPY';

async function main() {
  console.log(`\n📊 テクニカル指標テスト: ${pair}\n`);
  console.log('='.repeat(50));

  // RSI
  console.log('\n📈 RSI (相対力指数)');
  console.log('-'.repeat(30));
  const rsi = await indicators.getRSI(pair);
  if (rsi.error) {
    console.log(`   エラー: ${rsi.error}`);
  } else {
    console.log(`   値: ${rsi.value}`);
    console.log(`   シグナル: ${rsi.signal}`);
    console.log(`   判定: ${rsi.description}`);
  }

  // ボリンジャーバンド
  console.log('\n📊 ボリンジャーバンド');
  console.log('-'.repeat(30));
  const bb = await indicators.getBollingerBands(pair);
  if (bb.error) {
    console.log(`   エラー: ${bb.error}`);
  } else {
    console.log(`   上限: ¥${bb.upper?.toLocaleString()}`);
    console.log(`   中央: ¥${bb.middle?.toLocaleString()}`);
    console.log(`   下限: ¥${bb.lower?.toLocaleString()}`);
    console.log(`   現在: ¥${bb.currentPrice?.toLocaleString()}`);
    console.log(`   %B: ${(bb.percentB * 100).toFixed(1)}%`);
    console.log(`   バンド幅: ${bb.width}%`);
    console.log(`   判定: ${bb.description}`);
  }

  // MACD
  console.log('\n📉 MACD');
  console.log('-'.repeat(30));
  const macd = await indicators.getMACD(pair);
  if (macd.error) {
    console.log(`   エラー: ${macd.error}`);
  } else {
    console.log(`   MACD: ${macd.macd}`);
    console.log(`   シグナル: ${macd.signalLine}`);
    console.log(`   ヒストグラム: ${macd.histogram}`);
    console.log(`   クロス: ${macd.crossover}`);
    console.log(`   判定: ${macd.description}`);
  }

  // 総合シグナル
  console.log('\n🎯 総合シグナル');
  console.log('-'.repeat(30));
  const composite = await indicators.getCompositeSignal(pair);
  if (composite.error) {
    console.log(`   エラー: ${composite.error}`);
  } else {
    console.log(`   スコア: ${composite.score} (-100〜+100)`);
    console.log(`   アクション: ${composite.action}`);
    console.log(`   確信度: ${composite.confidence}`);
    console.log(`   判定: ${composite.description}`);
    console.log('\n   シグナル詳細:');
    composite.signals?.forEach(s => {
      const sign = s.score > 0 ? '+' : '';
      console.log(`      ${s.name}: ${sign}${s.score} (${s.reason})`);
    });
  }

  // 拡張トレンド分析
  console.log('\n🔮 拡張トレンド分析');
  console.log('-'.repeat(30));
  const bitflyer = require('./lib/bitflyer');
  const ticker = await bitflyer.getTicker(pair);
  const enhanced = await analysis.getEnhancedTrend(pair, ticker.ltp);
  console.log(`   現在価格: ¥${ticker.ltp.toLocaleString()}`);
  console.log(`   基本トレンド: ${enhanced.basicTrend}`);
  console.log(`   最終トレンド: ${enhanced.trend}`);
  console.log(`   取引許可: ${enhanced.shouldTrade ? '✅' : '❌'}`);
  console.log(`   推奨アクション: ${enhanced.tradeAction}`);
  console.log(`   理由: ${enhanced.reason}`);

  // 買いチェック
  console.log('\n💚 買いエントリーチェック');
  console.log('-'.repeat(30));
  const buyCheck = await analysis.shouldBuy(pair, ticker.ltp);
  console.log(`   買い可能: ${buyCheck.canBuy ? '✅ YES' : '❌ NO'}`);
  console.log(`   スコア: ${buyCheck.score}`);
  console.log(`   理由: ${buyCheck.reason}`);

  console.log('\n' + '='.repeat(50));
  console.log('✨ テスト完了\n');
}

main().catch(err => {
  console.error('エラー:', err);
  process.exit(1);
});
