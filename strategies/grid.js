const bitflyer = require('../lib/bitflyer');
const notify = require('../lib/notify');
const fs = require('fs');
const path = require('path');

const STATE_FILE = path.join(__dirname, '../data/grid-state.json');

class GridStrategy {
  constructor(pair, settings, dryRun = true) {
    this.pair = pair;
    this.settings = settings;
    this.dryRun = dryRun;
    this.state = this.loadState();
  }

  loadState() {
    if (fs.existsSync(STATE_FILE)) {
      const allState = JSON.parse(fs.readFileSync(STATE_FILE, 'utf8'));
      return allState[this.pair] || this.defaultState();
    }
    return this.defaultState();
  }

  defaultState() {
    return {
      gridOrders: [],      // 設置中のグリッド注文
      position: 0,         // 現在のポジション量
      avgBuyPrice: 0,      // 平均取得価格
      totalProfit: 0,      // 累計利益
      tradeCount: 0,       // 取引回数
      lastUpdate: null
    };
  }

  saveState() {
    let allState = {};
    if (fs.existsSync(STATE_FILE)) {
      allState = JSON.parse(fs.readFileSync(STATE_FILE, 'utf8'));
    }
    allState[this.pair] = {
      ...this.state,
      lastUpdate: new Date().toISOString()
    };
    fs.writeFileSync(STATE_FILE, JSON.stringify(allState, null, 2));
  }

  async execute() {
    try {
      const ticker = await bitflyer.getTicker(this.pair);
      const currentPrice = ticker.ltp;
      const symbol = this.pair.replace('_JPY', '');

      console.log(`\n[${symbol}] 現在価格: ¥${currentPrice.toLocaleString()}`);
      console.log(`[${symbol}] ポジション: ${this.state.position} (平均: ¥${this.state.avgBuyPrice.toLocaleString()})`);

      // グリッドレベルを計算
      const gridLevels = this.calculateGridLevels(currentPrice);
      
      // 買いシグナルチェック
      for (const level of gridLevels.buyLevels) {
        if (currentPrice <= level.price && this.state.position < this.settings.maxPosition) {
          await this.placeBuyOrder(level.price, this.settings.orderSize);
        }
      }

      // 売りシグナルチェック（利確）
      if (this.state.position > 0) {
        const targetPrice = this.state.avgBuyPrice * (1 + this.settings.takeProfitPercent / 100);
        if (currentPrice >= targetPrice) {
          await this.placeSellOrder(currentPrice, this.state.position);
        }
      }

      this.saveState();
      return { success: true, price: currentPrice };

    } catch (error) {
      console.error(`[${this.pair}] エラー:`, error.message);
      await notify.notifyError(`${this.pair}: ${error.message}`);
      return { success: false, error: error.message };
    }
  }

  calculateGridLevels(currentPrice) {
    const { gridCount, gridSpacingPercent } = this.settings;
    const spacing = currentPrice * (gridSpacingPercent / 100);

    const buyLevels = [];
    const sellLevels = [];

    for (let i = 1; i <= gridCount; i++) {
      buyLevels.push({
        level: i,
        price: Math.floor(currentPrice - spacing * i)
      });
      sellLevels.push({
        level: i,
        price: Math.floor(currentPrice + spacing * i)
      });
    }

    return { buyLevels, sellLevels };
  }

  async placeBuyOrder(price, size) {
    const symbol = this.pair.replace('_JPY', '');
    console.log(`[${symbol}] 🟢 買い注文: ${size} @ ¥${price.toLocaleString()}`);

    if (this.dryRun) {
      console.log(`[${symbol}] (DRY RUN - 実際の注文はスキップ)`);
      // シミュレーション
      const newPosition = this.state.position + size;
      this.state.avgBuyPrice = 
        (this.state.avgBuyPrice * this.state.position + price * size) / newPosition;
      this.state.position = newPosition;
      this.state.tradeCount++;
      
      await notify.notifyTrade(this.pair, 'BUY', price, size);
      return;
    }

    // 実際の注文
    const result = await bitflyer.sendOrder({
      product_code: this.pair,
      child_order_type: 'LIMIT',
      side: 'BUY',
      price: price,
      size: size
    });

    console.log(`[${symbol}] 注文ID: ${result.child_order_acceptance_id}`);
    await notify.notifyTrade(this.pair, 'BUY', price, size);
  }

  async placeSellOrder(price, size) {
    const symbol = this.pair.replace('_JPY', '');
    const profit = (price - this.state.avgBuyPrice) * size;
    
    console.log(`[${symbol}] 🔴 売り注文: ${size} @ ¥${price.toLocaleString()}`);
    console.log(`[${symbol}] 💰 予想利益: ¥${profit.toLocaleString()}`);

    if (this.dryRun) {
      console.log(`[${symbol}] (DRY RUN - 実際の注文はスキップ)`);
      this.state.position = 0;
      this.state.avgBuyPrice = 0;
      this.state.totalProfit += profit;
      this.state.tradeCount++;
      
      await notify.notifyTrade(this.pair, 'SELL', price, size, profit);
      return;
    }

    // 実際の注文（成行）
    const result = await bitflyer.sendOrder({
      product_code: this.pair,
      child_order_type: 'MARKET',
      side: 'SELL',
      size: size
    });

    this.state.totalProfit += profit;
    this.state.position = 0;
    this.state.avgBuyPrice = 0;
    this.state.tradeCount++;

    console.log(`[${symbol}] 注文ID: ${result.child_order_acceptance_id}`);
    await notify.notifyTrade(this.pair, 'SELL', price, size, profit);
  }

  getStats() {
    return {
      pair: this.pair,
      position: this.state.position,
      avgBuyPrice: this.state.avgBuyPrice,
      totalProfit: this.state.totalProfit,
      tradeCount: this.state.tradeCount,
      lastUpdate: this.state.lastUpdate
    };
  }
}

module.exports = GridStrategy;
