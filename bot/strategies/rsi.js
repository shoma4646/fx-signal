const bitflyer = require('../lib/bitflyer');
const notify = require('../lib/notify');
const indicators = require('../lib/indicators');
const fs = require('fs');
const path = require('path');

const STATE_FILE = path.join(__dirname, '../data/rsi-state.json');
const DEFAULT_COMMISSION_RATE = 0.0011; // 0.11%

class RSIStrategy {
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
      lastUpdate: null
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

      // RSI計算
      const rsiResult = await indicators.getRSI(this.pair, this.settings.rsiPeriod || 14);
      
      if (!rsiResult || rsiResult.error) {
        console.log(`[${this.name}] RSI計算中... ${rsiResult?.error || 'データ収集中'}`);
        return { success: true, action: 'waiting_data' };
      }

      const rsi = rsiResult.value;
      const { buyThreshold, sellThreshold, orderSize, maxPosition } = this.settings;

      console.log(`[${this.name}] 価格: ¥${currentPrice.toLocaleString()} | RSI: ${rsi.toFixed(1)} | ポジ: ${this.state.position}`);

      // 買いシグナル: RSI < buyThreshold (デフォルト30)
      if (rsi < buyThreshold && this.state.position < maxPosition) {
        // クールダウンチェック（連続買い防止）
        const cooldown = this.settings.cooldownMinutes || 30;
        if (this.state.lastAction === 'BUY' && this.state.lastActionTime) {
          const elapsed = (Date.now() - new Date(this.state.lastActionTime).getTime()) / 60000;
          if (elapsed < cooldown) {
            console.log(`[${this.name}] ⏳ クールダウン中 (残り${Math.ceil(cooldown - elapsed)}分)`);
            return { success: true, action: 'cooldown' };
          }
        }

        await this.executeBuy(currentPrice, rsi);
        return { success: true, action: 'buy', price: currentPrice, rsi };
      }

      // 売りシグナル: RSI > sellThreshold (デフォルト70) かつ ポジションあり
      if (rsi > sellThreshold && this.state.position > 0) {
        await this.executeSell(currentPrice, rsi);
        return { success: true, action: 'sell', price: currentPrice, rsi };
      }

      // 何もしない
      return { success: true, action: 'hold', price: currentPrice, rsi };

    } catch (error) {
      console.error(`[${this.name}] エラー:`, error.message);
      return { success: false, error: error.message };
    }
  }

  async executeBuy(price, rsi) {
    const size = this.settings.orderSize;
    const requiredJpy = price * size * 1.01;

    console.log(`[${this.name}] 🟢 買いシグナル! RSI: ${rsi.toFixed(1)} < ${this.settings.buyThreshold}`);

    // 残高チェック
    if (!this.dryRun) {
      try {
        const balances = await bitflyer.getBalance();
        const jpyBalance = balances.find(b => b.currency_code === 'JPY')?.available || 0;
        if (jpyBalance < requiredJpy) {
          console.log(`[${this.name}] ⚠️ JPY残高不足`);
          return;
        }
      } catch (e) {
        console.error(`[${this.name}] 残高取得失敗:`, e.message);
        return;
      }
    }

    if (this.dryRun) {
      console.log(`[${this.name}] (DRY RUN) 買い: ${size} @ ¥${price.toLocaleString()}`);
    } else {
      try {
        await bitflyer.sendOrder({
          product_code: this.pair,
          child_order_type: 'MARKET',
          side: 'BUY',
          size: size
        });
      } catch (error) {
        console.error(`[${this.name}] ❌ 買い注文失敗`);
        return;
      }
    }

    // 状態更新
    const newPosition = this.state.position + size;
    this.state.avgBuyPrice = 
      (this.state.avgBuyPrice * this.state.position + price * size) / newPosition;
    this.state.position = newPosition;
    this.state.tradeCount++;
    this.state.lastAction = 'BUY';
    this.state.lastActionTime = new Date().toISOString();
    this.saveState();

    await notify.notifyTrade(this.pair, 'BUY', price, size);
    await notify.sendDiscord(`🟢 **${this.name}** RSI買い! RSI=${rsi.toFixed(1)} @ ¥${price.toLocaleString()}`);
  }

  async executeSell(price, rsi) {
    const size = Math.min(this.settings.orderSize, this.state.position);
    const symbol = this.pair.replace('_JPY', '');

    // 利益計算
    const grossProfit = (price - this.state.avgBuyPrice) * size;
    const fee = (this.state.avgBuyPrice + price) * size * DEFAULT_COMMISSION_RATE;
    const profit = grossProfit - fee;

    console.log(`[${this.name}] 🔴 売りシグナル! RSI: ${rsi.toFixed(1)} > ${this.settings.sellThreshold} (損益: ¥${profit.toFixed(0)})`);

    // 残高チェック
    if (!this.dryRun) {
      try {
        const balances = await bitflyer.getBalance();
        const cryptoBalance = balances.find(b => b.currency_code === symbol)?.available || 0;
        if (cryptoBalance < size) {
          console.log(`[${this.name}] ⚠️ ${symbol}残高不足`);
          return;
        }
      } catch (e) {
        console.error(`[${this.name}] 残高取得失敗:`, e.message);
        return;
      }
    }

    if (this.dryRun) {
      console.log(`[${this.name}] (DRY RUN) 売り: ${size} @ ¥${price.toLocaleString()}`);
    } else {
      try {
        await bitflyer.sendOrder({
          product_code: this.pair,
          child_order_type: 'MARKET',
          side: 'SELL',
          size: size
        });
      } catch (error) {
        console.error(`[${this.name}] ❌ 売り注文失敗`);
        return;
      }
    }

    // 状態更新
    this.state.position -= size;
    if (this.state.position <= 0) {
      this.state.position = 0;
      this.state.avgBuyPrice = 0;
    }
    this.state.totalProfit += profit;
    this.state.tradeCount++;
    this.state.lastAction = 'SELL';
    this.state.lastActionTime = new Date().toISOString();
    this.saveState();

    await notify.notifyTrade(this.pair, 'SELL', price, size, profit);
    await notify.sendDiscord(`🔴 **${this.name}** RSI売り! RSI=${rsi.toFixed(1)} @ ¥${price.toLocaleString()} (損益: ¥${profit.toFixed(0)})`);
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
      lastUpdate: this.state.lastUpdate
    };
  }
}

module.exports = RSIStrategy;
