/**
 * EMAクロス・トレンドフォロー戦略
 * 
 * 短期EMA(9)が長期EMA(21)を上抜け → 買い（ゴールデンクロス）
 * 短期EMA(9)が長期EMA(21)を下抜け → 売り（デッドクロス）
 * 
 * @author Stella ✨
 * @created 2026-02-26
 */

const bitflyer = require('../lib/bitflyer');
const notify = require('../lib/notify');
const indicators = require('../lib/indicators');
const fs = require('fs');
const path = require('path');

const STATE_FILE = path.join(__dirname, '../data/ema-trend-state.json');
const DEFAULT_COMMISSION_RATE = 0.0011; // 0.11%

// リスク管理の定数
const STOP_LOSS_PERCENT = -5;         // 損切りライン: -5%
const TRAILING_TRIGGER = 8;           // トレーリング発動: +8%
const TRAILING_STOP = 5;              // トレーリング確定: +5%

// 注文サイズを丸める（bitFlyer最小単位: 0.0000001）
function roundSize(size) {
  return Math.floor(size * 10000000) / 10000000;
}

class EMATrendStrategy {
  constructor(name, pair, settings, dryRun = true) {
    this.name = name;
    this.pair = pair;
    this.settings = settings;
    this.dryRun = dryRun;
    this.state = this.loadState();
  }

  loadState() {
    if (fs.existsSync(STATE_FILE)) {
      const allState = JSON.parse(fs.readFileSync(STATE_FILE, 'utf8'));
      return allState[this.name] || this.defaultState();
    }
    return this.defaultState();
  }

  defaultState() {
    return {
      position: 0,
      avgBuyPrice: 0,
      totalProfit: 0,
      tradeCount: 0,
      lastAction: null,
      lastActionTime: null,
      lastUpdate: null,
      lastCrossover: null,    // 最後のクロスオーバー（golden/death）
      maxProfitPercent: 0,
      trailingActive: false
    };
  }

  saveState() {
    let allState = {};
    if (fs.existsSync(STATE_FILE)) {
      allState = JSON.parse(fs.readFileSync(STATE_FILE, 'utf8'));
    }
    allState[this.name] = {
      ...this.state,
      pair: this.pair,
      lastUpdate: new Date().toISOString()
    };
    fs.writeFileSync(STATE_FILE, JSON.stringify(allState, null, 2));
  }

  async execute() {
    try {
      const ticker = await bitflyer.getTicker(this.pair);
      const currentPrice = ticker.ltp;

      // EMAとMACDを取得
      const macd = await indicators.getMACD(this.pair);
      
      if (macd.error) {
        console.log(`[${this.name}] EMA計算中... ${macd.error}`);
        return { success: true, action: 'waiting_data' };
      }

      const { crossover, histogram, trend } = macd;
      const { orderSize, maxPosition } = this.settings;

      const trendStr = histogram > 0 ? '📈' : histogram < 0 ? '📉' : '➖';
      console.log(`[${this.name}] 価格: ¥${currentPrice.toLocaleString()} | トレンド: ${trendStr} | クロス: ${crossover} | ポジ: ${this.state.position}`);

      // === ポジションがある場合のリスク管理 ===
      if (this.state.position > 0 && this.state.avgBuyPrice > 0) {
        const profitPercent = ((currentPrice - this.state.avgBuyPrice) / this.state.avgBuyPrice) * 100;
        
        // 最大利益%を更新（トレーリング用）
        if (profitPercent > this.state.maxProfitPercent) {
          this.state.maxProfitPercent = profitPercent;
          if (profitPercent >= TRAILING_TRIGGER && !this.state.trailingActive) {
            this.state.trailingActive = true;
            console.log(`[${this.name}] 🎯 トレーリング発動！ +${profitPercent.toFixed(1)}% 到達`);
          }
          this.saveState();
        }

        // 🛑 損切り
        if (profitPercent <= STOP_LOSS_PERCENT) {
          console.log(`[${this.name}] 🛑 損切り発動！ ${profitPercent.toFixed(1)}%`);
          await this.executeStopLoss(currentPrice, profitPercent);
          return { success: true, action: 'stop_loss' };
        }

        // 📈 トレーリングストップ
        if (this.state.trailingActive && profitPercent <= TRAILING_STOP) {
          console.log(`[${this.name}] 📈 トレーリング利確！ ${profitPercent.toFixed(1)}%`);
          await this.executeTrailingStop(currentPrice, profitPercent);
          return { success: true, action: 'trailing_stop' };
        }

        // デッドクロスで売り
        if (crossover === 'death') {
          console.log(`[${this.name}] 💀 デッドクロス検知！トレンド転換で売り`);
          await this.executeSell(currentPrice, 'デッドクロス');
          return { success: true, action: 'sell_death_cross' };
        }
      }

      // === ゴールデンクロスで買い ===
      if (crossover === 'golden' && this.state.position < maxPosition) {
        // 同じクロスで連続買いを防ぐ
        if (this.state.lastCrossover === 'golden' && this.state.lastAction === 'BUY') {
          const elapsed = (Date.now() - new Date(this.state.lastActionTime).getTime()) / 60000;
          if (elapsed < 60) { // 1時間以内は買わない
            console.log(`[${this.name}] ⏳ ゴールデンクロス後クールダウン中`);
            return { success: true, action: 'cooldown' };
          }
        }

        console.log(`[${this.name}] 🔥 ゴールデンクロス検知！買いエントリー`);
        await this.executeBuy(currentPrice, 'ゴールデンクロス');
        this.state.lastCrossover = 'golden';
        this.saveState();
        return { success: true, action: 'buy_golden_cross' };
      }

      // クロスオーバー状態を記録
      if (crossover !== 'none') {
        this.state.lastCrossover = crossover;
        this.saveState();
      }

      return { success: true, action: 'hold' };
    } catch (error) {
      console.error(`[${this.name}] エラー:`, error.message);
      return { success: false, error: error.message };
    }
  }

  async executeBuy(price, reason) {
    const size = this.settings.orderSize;
    const requiredJpy = price * size * 1.01;

    console.log(`[${this.name}] 🟢 買い: ${reason}`);

    if (!this.dryRun) {
      try {
        const balances = await bitflyer.getBalance();
        const jpyBalance = balances.find(b => b.currency_code === 'JPY')?.available || 0;
        if (jpyBalance < requiredJpy) {
          console.log(`[${this.name}] ⚠️ JPY残高不足`);
          return;
        }

        await bitflyer.sendOrder({
          product_code: this.pair,
          child_order_type: 'MARKET',
          side: 'BUY',
          size: size
        });
      } catch (error) {
        console.error(`[${this.name}] ❌ 買い注文失敗:`, error.message);
        return;
      }
    } else {
      console.log(`[${this.name}] (DRY RUN) 買い: ${size} @ ¥${price.toLocaleString()}`);
    }

    // 状態更新
    const newPosition = this.state.position + size;
    this.state.avgBuyPrice = 
      (this.state.avgBuyPrice * this.state.position + price * size) / newPosition;
    this.state.position = newPosition;
    this.state.tradeCount++;
    this.state.lastAction = 'BUY';
    this.state.lastActionTime = new Date().toISOString();
    this.state.maxProfitPercent = 0;
    this.state.trailingActive = false;
    this.saveState();

    await notify.notifyTrade(this.pair, 'BUY', price, size);
    await notify.sendDiscord(`🟢 **${this.name}** ${reason}で買い！ @ ¥${price.toLocaleString()}`);
  }

  async executeSell(price, reason) {
    const size = roundSize(this.state.position);
    if (size <= 0) return;

    const grossProfit = (price - this.state.avgBuyPrice) * size;
    const fee = (this.state.avgBuyPrice + price) * size * DEFAULT_COMMISSION_RATE;
    const profit = grossProfit - fee;
    const profitPercent = ((price - this.state.avgBuyPrice) / this.state.avgBuyPrice) * 100;

    console.log(`[${this.name}] 🔴 売り: ${reason} (損益: ¥${profit.toFixed(0)})`);

    if (!this.dryRun) {
      try {
        await bitflyer.sendOrder({
          product_code: this.pair,
          child_order_type: 'MARKET',
          side: 'SELL',
          size: size
        });
      } catch (error) {
        console.error(`[${this.name}] ❌ 売り注文失敗:`, error.message);
        return;
      }
    } else {
      console.log(`[${this.name}] (DRY RUN) 売り: ${size} @ ¥${price.toLocaleString()}`);
    }

    // 状態更新
    this.state.totalProfit += profit;
    this.state.tradeCount++;
    this.state.position = 0;
    this.state.avgBuyPrice = 0;
    this.state.maxProfitPercent = 0;
    this.state.trailingActive = false;
    this.state.lastAction = 'SELL';
    this.state.lastActionTime = new Date().toISOString();
    this.state.lastCrossover = 'death';
    this.saveState();

    await notify.notifyTrade(this.pair, 'SELL', price, size, profit);
    await notify.sendDiscord(`🔴 **${this.name}** ${reason}で売り @ ¥${price.toLocaleString()} (${profitPercent >= 0 ? '+' : ''}${profitPercent.toFixed(1)}% / ¥${profit.toFixed(0)})`);
  }

  async executeStopLoss(price, profitPercent) {
    const size = roundSize(this.state.position);
    const grossProfit = (price - this.state.avgBuyPrice) * size;
    const fee = (this.state.avgBuyPrice + price) * size * DEFAULT_COMMISSION_RATE;
    const profit = grossProfit - fee;

    if (!this.dryRun) {
      try {
        await bitflyer.sendOrder({
          product_code: this.pair,
          child_order_type: 'MARKET',
          side: 'SELL',
          size: size
        });
      } catch (error) {
        console.error(`[${this.name}] ❌ 損切り注文失敗:`, error.message);
        return;
      }
    }

    // 状態リセット
    this.state.totalProfit += profit;
    this.state.tradeCount++;
    this.state.position = 0;
    this.state.avgBuyPrice = 0;
    this.state.maxProfitPercent = 0;
    this.state.trailingActive = false;
    this.state.lastAction = 'STOP_LOSS';
    this.state.lastActionTime = new Date().toISOString();
    this.saveState();

    await notify.notifyTrade(this.pair, 'SELL', price, size, profit);
    await notify.sendDiscord(`🛑 **${this.name}** 損切り！ ${profitPercent.toFixed(1)}% @ ¥${price.toLocaleString()} (損失: ¥${Math.abs(profit).toFixed(0)})`);
  }

  async executeTrailingStop(price, profitPercent) {
    const size = roundSize(this.state.position);
    const grossProfit = (price - this.state.avgBuyPrice) * size;
    const fee = (this.state.avgBuyPrice + price) * size * DEFAULT_COMMISSION_RATE;
    const profit = grossProfit - fee;

    if (!this.dryRun) {
      try {
        await bitflyer.sendOrder({
          product_code: this.pair,
          child_order_type: 'MARKET',
          side: 'SELL',
          size: size
        });
      } catch (error) {
        console.error(`[${this.name}] ❌ トレーリング注文失敗:`, error.message);
        return;
      }
    }

    // 状態リセット
    this.state.totalProfit += profit;
    this.state.tradeCount++;
    this.state.position = 0;
    this.state.avgBuyPrice = 0;
    const maxProfit = this.state.maxProfitPercent;
    this.state.maxProfitPercent = 0;
    this.state.trailingActive = false;
    this.state.lastAction = 'TRAILING_STOP';
    this.state.lastActionTime = new Date().toISOString();
    this.saveState();

    await notify.notifyTrade(this.pair, 'SELL', price, size, profit);
    await notify.sendDiscord(`📈 **${this.name}** トレーリング利確！ +${profitPercent.toFixed(1)}% (最大+${maxProfit.toFixed(1)}%) @ ¥${price.toLocaleString()} (利益: ¥${profit.toFixed(0)})`);
  }

  getStats() {
    return {
      name: this.name,
      pair: this.pair,
      position: this.state.position,
      avgBuyPrice: this.state.avgBuyPrice,
      totalProfit: this.state.totalProfit,
      tradeCount: this.state.tradeCount,
      lastAction: this.state.lastAction,
      lastUpdate: this.state.lastUpdate,
      lastCrossover: this.state.lastCrossover,
      maxProfitPercent: this.state.maxProfitPercent,
      trailingActive: this.state.trailingActive
    };
  }
}

module.exports = EMATrendStrategy;
