const bitflyer = require('../lib/bitflyer');
const notify = require('../lib/notify');
const analysis = require('../lib/analysis');
const safety = require('../lib/safety');
const fs = require('fs');
const path = require('path');

const STATE_FILE = path.join(__dirname, '../data/grid-state.json');
const REBALANCE_THRESHOLD = 5; // 5%ずれたらリバランス
const STOP_LOSS_THRESHOLD = -5; // -5%で損切り（安全重視）

class GridStrategy {
  constructor(name, pair, settings, dryRun = true) {
    this.name = name;      // 戦略名 (例: ETH_WIDE)
    this.pair = pair;      // 通貨ペア (例: ETH_JPY)
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
      basePrice: null,           // グリッドの基準価格（固定）
      gridLevels: [],            // グリッドレベル情報
      position: 0,               // 現在のポジション量
      avgBuyPrice: 0,            // 平均取得価格
      totalProfit: 0,            // 累計利益
      tradeCount: 0,             // 取引回数
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

  // グリッドを初期化（移動平均で基準価格を設定）
  async initializeGrid(currentPrice) {
    const { gridCount, gridSpacingPercent } = this.settings;
    
    // 移動平均を取得して基準価格に
    const ma = await analysis.getMovingAverage(this.pair, 24);
    this.state.basePrice = ma || currentPrice;
    this.state.gridLevels = [];
    
    const priceSource = ma ? '24h移動平均' : '現在価格';

    // 買いグリッド（下方向）
    for (let i = 1; i <= gridCount; i++) {
      const price = Math.floor(currentPrice * (1 - gridSpacingPercent * i / 100));
      this.state.gridLevels.push({
        type: 'BUY',
        level: i,
        price: price,
        triggered: false
      });
    }

    // 売りグリッド（上方向）
    for (let i = 1; i <= gridCount; i++) {
      const price = Math.floor(currentPrice * (1 + gridSpacingPercent * i / 100));
      this.state.gridLevels.push({
        type: 'SELL',
        level: i,
        price: price,
        triggered: false
      });
    }

    console.log(`[${this.name}] 📐 グリッド初期化 基準: ¥${this.state.basePrice.toLocaleString()} (${priceSource}, 間隔: ${gridSpacingPercent}%)`);
    this.state.gridLevels.forEach(g => {
      const emoji = g.type === 'BUY' ? '🟢' : '🔴';
      console.log(`[${this.name}]    ${emoji} ${g.type} Lv${g.level}: ¥${g.price.toLocaleString()}`);
    });

    this.saveState();
  }

  async execute() {
    try {
      const ticker = await bitflyer.getTicker(this.pair);
      const currentPrice = ticker.ltp;
      const symbol = this.pair.replace('_JPY', '');

      // 初回 or グリッド未設定なら初期化
      if (!this.state.basePrice || this.state.gridLevels.length === 0) {
        await this.initializeGrid(currentPrice);
        return { success: true, price: currentPrice, action: 'initialized' };
      }

      // 自動リバランスチェック（基準から5%以上ずれたら）
      const deviation = analysis.calculateDeviation(currentPrice, this.state.basePrice);
      
      // 損切りチェック（ポジションありで-7%以下）
      if (this.state.position > 0 && deviation <= STOP_LOSS_THRESHOLD) {
        console.log(`[${this.name}] 🛑 損切り発動！（乖離: ${deviation.toFixed(1)}%）`);
        await this.executeStopLoss(currentPrice);
        return { success: true, price: currentPrice, action: 'stop_loss' };
      }
      
      if (Math.abs(deviation) >= REBALANCE_THRESHOLD && this.state.position === 0) {
        console.log(`[${this.name}] 🔄 自動リバランス（乖離: ${deviation.toFixed(1)}%）`);
        await notify.sendDiscord(`🔄 **${this.name}** 自動リバランス（乖離: ${deviation.toFixed(1)}%）`);
        await this.initializeGrid(currentPrice);
        return { success: true, price: currentPrice, action: 'rebalanced' };
      }

      console.log(`[${this.name}] 現在: ¥${currentPrice.toLocaleString()} | 基準: ¥${this.state.basePrice.toLocaleString()} | ポジ: ${this.state.position} | 乖離: ${deviation.toFixed(1)}%`);

      // 買いグリッドチェック
      const buyLevels = this.state.gridLevels
        .filter(g => g.type === 'BUY' && !g.triggered)
        .sort((a, b) => b.price - a.price); // 高い方から

      for (const level of buyLevels) {
        if (currentPrice <= level.price) {
          if (this.state.position < this.settings.maxPosition) {
            await this.executeBuy(level, currentPrice);
          }
        }
      }

      // 売りグリッドチェック（ポジションがある時のみ）
      if (this.state.position > 0) {
        const sellLevels = this.state.gridLevels
          .filter(g => g.type === 'SELL' && !g.triggered)
          .sort((a, b) => a.price - b.price); // 安い方から

        for (const level of sellLevels) {
          if (currentPrice >= level.price) {
            await this.executeSell(level, currentPrice);
            break; // 一度に1つだけ売る
          }
        }

        // 利確チェック（グリッド外でも平均+X%で利確）
        const takeProfitPrice = this.state.avgBuyPrice * (1 + this.settings.takeProfitPercent / 100);
        if (currentPrice >= takeProfitPrice && this.state.position > 0) {
          await this.executeTakeProfit(currentPrice);
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

  async executeBuy(level, currentPrice) {
    const size = this.settings.orderSize;
    const price = currentPrice; // 成行相当

    console.log(`[${this.name}] 🟢 買いシグナル Lv${level.level} @ ¥${price.toLocaleString()}`);

    if (this.dryRun) {
      console.log(`[${this.name}] (DRY RUN)`);
    } else {
      await bitflyer.sendOrder({
        product_code: this.pair,
        child_order_type: 'MARKET',
        side: 'BUY',
        size: size
      });
    }

    // 状態更新
    const newPosition = this.state.position + size;
    this.state.avgBuyPrice = 
      (this.state.avgBuyPrice * this.state.position + price * size) / newPosition;
    this.state.position = newPosition;
    this.state.tradeCount++;
    level.triggered = true;

    await notify.notifyTrade(this.pair, 'BUY', price, size);
  }

  async executeSell(level, currentPrice) {
    const size = Math.min(this.settings.orderSize, this.state.position);
    const profit = (currentPrice - this.state.avgBuyPrice) * size;

    console.log(`[${this.name}] 🔴 売りシグナル Lv${level.level} @ ¥${currentPrice.toLocaleString()} (損益: ¥${profit.toFixed(0)})`);

    if (this.dryRun) {
      console.log(`[${this.name}] (DRY RUN)`);
    } else {
      await bitflyer.sendOrder({
        product_code: this.pair,
        child_order_type: 'MARKET',
        side: 'SELL',
        size: size
      });
    }

    // 状態更新
    this.state.position -= size;
    if (this.state.position <= 0) {
      this.state.position = 0;
      this.state.avgBuyPrice = 0;
    }
    this.state.totalProfit += profit;
    this.state.tradeCount++;
    level.triggered = true;

    await notify.notifyTrade(this.pair, 'SELL', currentPrice, size, profit);
    await safety.recordTrade(profit, 5000);
  }

  async executeTakeProfit(currentPrice) {
    const size = this.state.position;
    const profit = (currentPrice - this.state.avgBuyPrice) * size;

    console.log(`[${this.name}] 💰 利確！ @ ¥${currentPrice.toLocaleString()} (損益: ¥${profit.toFixed(0)})`);

    if (this.dryRun) {
      console.log(`[${this.name}] (DRY RUN)`);
    } else {
      await bitflyer.sendOrder({
        product_code: this.pair,
        child_order_type: 'MARKET',
        side: 'SELL',
        size: size
      });
    }

    // 状態更新 & グリッドリセット
    this.state.totalProfit += profit;
    this.state.tradeCount++;
    this.state.position = 0;
    this.state.avgBuyPrice = 0;
    this.state.basePrice = null; // 次回グリッド再初期化
    this.state.gridLevels = [];

    await notify.notifyTrade(this.pair, 'SELL', currentPrice, size, profit);
    await notify.sendDiscord(`💰 **${this.name} 利確完了！** グリッドをリセットします`);
    await safety.recordTrade(profit, 5000);
  }

  // 損切り実行
  async executeStopLoss(currentPrice) {
    const size = this.state.position;
    const loss = (currentPrice - this.state.avgBuyPrice) * size;

    console.log(`[${this.name}] 🛑 損切り @ ¥${currentPrice.toLocaleString()} (損失: ¥${loss.toFixed(0)})`);

    if (this.dryRun) {
      console.log(`[${this.name}] (DRY RUN)`);
    } else {
      await bitflyer.sendOrder({
        product_code: this.pair,
        child_order_type: 'MARKET',
        side: 'SELL',
        size: size
      });
    }

    // 状態更新 & グリッドリセット
    this.state.totalProfit += loss;
    this.state.tradeCount++;
    this.state.position = 0;
    this.state.avgBuyPrice = 0;
    this.state.basePrice = null;
    this.state.gridLevels = [];

    await notify.notifyTrade(this.pair, 'SELL', currentPrice, size, loss);
    await notify.sendDiscord(`🛑 **${this.name} 損切り！** ¥${loss.toFixed(0)} | 下落トレンドのためグリッドリセット`);
    await safety.recordTrade(loss, 5000);
    
    this.saveState();
  }

  // グリッドを手動リセット
  resetGrid() {
    this.state.basePrice = null;
    this.state.gridLevels = [];
    this.saveState();
  }

  getStats() {
    return {
      name: this.name,
      pair: this.pair,
      basePrice: this.state.basePrice,
      position: this.state.position,
      avgBuyPrice: this.state.avgBuyPrice,
      totalProfit: this.state.totalProfit,
      tradeCount: this.state.tradeCount,
      gridLevels: this.state.gridLevels,
      lastUpdate: this.state.lastUpdate
    };
  }
}

module.exports = GridStrategy;
