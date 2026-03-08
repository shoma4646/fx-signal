const bitflyer = require('../lib/bitflyer');
const notify = require('../lib/notify');
const indicators = require('../lib/indicators');
const fs = require('fs');
const path = require('path');

const STATE_FILE = path.join(__dirname, '../data/rsi-state.json');
const DEFAULT_COMMISSION_RATE = 0.0011; // 0.11%

// ステラ流・最強戦略の定数
const STOP_LOSS_PERCENT = -7;        // 損切りライン: -7%
const MIN_PROFIT_PERCENT = 0.5;      // 最低利益: +0.5%（手数料負け防止）
const TRAILING_TRIGGER = 5;          // トレーリング発動: +5%
const TRAILING_STOP = 3;             // トレーリング確定: +3%
const SUPER_OVERSOLD_RSI = 25;       // 超売られすぎ（ナンピン条件）

// 注文サイズを丸める（bitFlyer最小単位: 0.0000001）
function roundSize(size) {
  return Math.floor(size * 10000000) / 10000000;
}

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
      lastUpdate: null,
      // ステラ流追加
      maxProfitPercent: 0,    // 最大到達利益%（トレーリング用）
      trailingActive: false   // トレーリング発動フラグ
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

      // 複合スコアを使用するか（設定で切り替え可能）
      const useComposite = this.settings.useCompositeScore !== false;
      
      if (useComposite) {
        return await this.executeWithCompositeScore(currentPrice);
      } else {
        return await this.executeWithRSI(currentPrice);
      }
    } catch (error) {
      console.error(`[${this.name}] エラー:`, error.message);
      return { success: false, error: error.message };
    }
  }

  /**
   * 複合スコアを使った判断ロジック
   */
  async executeWithCompositeScore(currentPrice) {
    const composite = await indicators.getCompositeScore(this.pair);
    
    if (!composite || composite.error) {
      console.log(`[${this.name}] スコア計算中... ${composite?.error || 'データ収集中'}`);
      return { success: true, action: 'waiting_data' };
    }

    const { score, signal, components } = composite;
    const { orderSize, maxPosition } = this.settings;
    
    // 閾値（デフォルト: 買い+30、売り-30）
    const buyScoreThreshold = this.settings.buyScoreThreshold || 30;
    const sellScoreThreshold = this.settings.sellScoreThreshold || -30;

    const rsiStr = components.rsi?.value?.toFixed(1) || 'N/A';
    const bbStr = components.bb?.percentB ? `${(components.bb.percentB * 100).toFixed(0)}%` : 'N/A';
    
    console.log(`[${this.name}] 価格: ¥${currentPrice.toLocaleString()} | スコア: ${score} (${signal}) | RSI: ${rsiStr} | BB%: ${bbStr} | ポジ: ${this.state.position}`);

    // === ステラ流: ポジションがある場合の判断 ===
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

      // 🛑 損切り: -7%で強制売り
      if (profitPercent <= STOP_LOSS_PERCENT) {
        console.log(`[${this.name}] 🛑 損切り発動！ ${profitPercent.toFixed(1)}% <= ${STOP_LOSS_PERCENT}%`);
        await this.executeStopLoss(currentPrice, profitPercent);
        return { success: true, action: 'stop_loss', price: currentPrice, profitPercent };
      }

      // 📈 トレーリングストップ: +5%到達後、+3%まで下がったら利確
      if (this.state.trailingActive && profitPercent <= TRAILING_STOP) {
        console.log(`[${this.name}] 📈 トレーリング利確！ ${profitPercent.toFixed(1)}% (最大: +${this.state.maxProfitPercent.toFixed(1)}%)`);
        await this.executeTrailingStop(currentPrice, profitPercent);
        return { success: true, action: 'trailing_stop', price: currentPrice, profitPercent };
      }
    }

    // 🔥 ナンピン: RSI超売られすぎ（<25）でポジションあれば追加買い
    const rsiValue = components.rsi?.value;
    if (rsiValue && rsiValue < SUPER_OVERSOLD_RSI && this.state.position > 0 && this.state.position < maxPosition) {
      const currentProfitPercent = ((currentPrice - this.state.avgBuyPrice) / this.state.avgBuyPrice) * 100;
      if (currentProfitPercent < -3) {  // -3%以上下落時のみナンピン
        console.log(`[${this.name}] 🔥 ナンピン条件! RSI=${rsiValue.toFixed(1)} < ${SUPER_OVERSOLD_RSI}, 損失=${currentProfitPercent.toFixed(1)}%`);
        await this.executeBuyWithScore(currentPrice, composite, 1);
        return { success: true, action: 'averaging_down', price: currentPrice, rsi: rsiValue };
      }
    }

    // 買いシグナル: スコアが閾値以上
    if (score >= buyScoreThreshold && this.state.position < maxPosition) {
      // クールダウンチェック
      const cooldown = this.settings.cooldownMinutes || 30;
      if (this.state.lastAction === 'BUY' && this.state.lastActionTime) {
        const elapsed = (Date.now() - new Date(this.state.lastActionTime).getTime()) / 60000;
        if (elapsed < cooldown) {
          console.log(`[${this.name}] ⏳ クールダウン中 (残り${Math.ceil(cooldown - elapsed)}分)`);
          return { success: true, action: 'cooldown' };
        }
      }

      // 強い買いシグナル時はサイズを増やす（オプション）
      const sizeMultiplier = (signal === 'strong_buy' && this.settings.strongSignalMultiplier) 
        ? this.settings.strongSignalMultiplier : 1;
      
      await this.executeBuyWithScore(currentPrice, composite, sizeMultiplier);
      return { success: true, action: 'buy', price: currentPrice, score, signal };
    }

    // 売りシグナル: スコアが閾値以下 かつ ポジションあり
    if (score <= sellScoreThreshold && this.state.position > 0) {
      await this.executeSellWithScore(currentPrice, composite);
      return { success: true, action: 'sell', price: currentPrice, score, signal };
    }

    // 何もしない
    return { success: true, action: 'hold', price: currentPrice, score, signal };
  }

  /**
   * 従来のRSIのみを使った判断ロジック（後方互換性）
   */
  async executeWithRSI(currentPrice) {
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
  }

  /**
   * 複合スコアを使った買い実行
   */
  async executeBuyWithScore(price, composite, sizeMultiplier = 1) {
    const { score, signal, description } = composite;
    const size = this.settings.orderSize * sizeMultiplier;
    const requiredJpy = price * size * 1.01;

    console.log(`[${this.name}] 🟢 買いシグナル! ${description}`);

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
    await notify.sendDiscord(`🟢 **${this.name}** 複合買い! スコア=${score} (${signal}) @ ¥${price.toLocaleString()}\n${description}`);
  }

  /**
   * 複合スコアを使った売り実行
   */
  async executeSellWithScore(price, composite) {
    const { score, signal, description } = composite;
    const size = roundSize(Math.min(this.settings.orderSize, this.state.position));
    const symbol = this.pair.replace('_JPY', '');

    // 利益計算
    const grossProfit = (price - this.state.avgBuyPrice) * size;
    const fee = (this.state.avgBuyPrice + price) * size * DEFAULT_COMMISSION_RATE;
    const profit = grossProfit - fee;

    console.log(`[${this.name}] 🔴 売りシグナル! ${description} (損益: ¥${profit.toFixed(0)})`);

    // 🛡️ 最低利益チェック: +0.5%未満なら売らない
    const profitPercent = ((price - this.state.avgBuyPrice) / this.state.avgBuyPrice) * 100;
    if (profitPercent < MIN_PROFIT_PERCENT) {
      console.log(`[${this.name}] ⏸️ 売り見送り（+${profitPercent.toFixed(2)}% < +${MIN_PROFIT_PERCENT}%）もう少し上がるまで待機`);
      return;
    }

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
      this.state.maxProfitPercent = 0;
      this.state.trailingActive = false;
    }
    this.state.totalProfit += profit;
    this.state.tradeCount++;
    this.state.lastAction = 'SELL';
    this.state.lastActionTime = new Date().toISOString();
    this.saveState();

    await notify.notifyTrade(this.pair, 'SELL', price, size, profit);
    await notify.sendDiscord(`🔴 **${this.name}** 複合売り! スコア=${score} (${signal}) @ ¥${price.toLocaleString()} (+${profitPercent.toFixed(1)}% / ¥${profit.toFixed(0)})\n${description}`);
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
    const size = roundSize(Math.min(this.settings.orderSize, this.state.position));
    const symbol = this.pair.replace('_JPY', '');

    // 利益計算
    const grossProfit = (price - this.state.avgBuyPrice) * size;
    const fee = (this.state.avgBuyPrice + price) * size * DEFAULT_COMMISSION_RATE;
    const profit = grossProfit - fee;

    console.log(`[${this.name}] 🔴 売りシグナル! RSI: ${rsi.toFixed(1)} > ${this.settings.sellThreshold} (損益: ¥${profit.toFixed(0)})`);

    // 🛡️ 最低利益チェック: +0.5%未満なら売らない
    const profitPercent = ((price - this.state.avgBuyPrice) / this.state.avgBuyPrice) * 100;
    if (profitPercent < MIN_PROFIT_PERCENT) {
      console.log(`[${this.name}] ⏸️ 売り見送り（+${profitPercent.toFixed(2)}% < +${MIN_PROFIT_PERCENT}%）もう少し上がるまで待機`);
      return;
    }

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
      this.state.maxProfitPercent = 0;
      this.state.trailingActive = false;
    }
    this.state.totalProfit += profit;
    this.state.tradeCount++;
    this.state.lastAction = 'SELL';
    this.state.lastActionTime = new Date().toISOString();
    this.saveState();

    await notify.notifyTrade(this.pair, 'SELL', price, size, profit);
    await notify.sendDiscord(`🔴 **${this.name}** RSI売り! RSI=${rsi.toFixed(1)} @ ¥${price.toLocaleString()} (+${profitPercent.toFixed(1)}% / ¥${profit.toFixed(0)})`);
  }

  /**
   * 🛑 損切り実行
   */
  async executeStopLoss(price, profitPercent) {
    const size = roundSize(this.state.position);
    if (size <= 0) {
      this.state.position = 0;
      this.state.trailingActive = false;
      this.saveState();
      return;
    }

    const symbol = this.pair.replace('_JPY', '');
    const grossProfit = (price - this.state.avgBuyPrice) * size;
    const fee = (this.state.avgBuyPrice + price) * size * DEFAULT_COMMISSION_RATE;
    const profit = grossProfit - fee;

    console.log(`[${this.name}] 🛑 損切り実行: ${size} ${symbol} @ ¥${price.toLocaleString()} (損失: ¥${profit.toFixed(0)})`);

    if (!this.dryRun) {
      // 残高チェック
      try {
        const balances = await bitflyer.getBalance();
        const cryptoBalance = balances.find(b => b.currency_code === symbol)?.available || 0;
        if (cryptoBalance < size) {
          console.log(`[${this.name}] ⚠️ ${symbol}残高不足 - stateリセット`);
          this.state.position = 0;
          this.state.avgBuyPrice = 0;
          this.state.trailingActive = false;
          this.saveState();
          return;
        }
      } catch (e) {
        console.error(`[${this.name}] 残高取得失敗:`, e.message);
      }

      try {
        await bitflyer.sendOrder({
          product_code: this.pair,
          child_order_type: 'MARKET',
          side: 'SELL',
          size: size
        });
      } catch (error) {
        console.error(`[${this.name}] ❌ 損切り注文失敗`);
        this.state.position = 0;
        this.state.avgBuyPrice = 0;
        this.state.trailingActive = false;
        this.saveState();
        return;
      }
    } else {
      console.log(`[${this.name}] (DRY RUN) 損切り: ${size} @ ¥${price.toLocaleString()}`);
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
    await notify.sendDiscord(`🛑 **${this.name}** 損切り！ ${profitPercent.toFixed(1)}% @ ¥${price.toLocaleString()} (損失: ¥${profit.toFixed(0)})`);
  }

  /**
   * 📈 トレーリングストップ実行
   */
  async executeTrailingStop(price, profitPercent) {
    const size = roundSize(this.state.position);
    if (size <= 0) {
      this.state.position = 0;
      this.state.trailingActive = false;
      this.saveState();
      return;
    }

    const symbol = this.pair.replace('_JPY', '');
    const grossProfit = (price - this.state.avgBuyPrice) * size;
    const fee = (this.state.avgBuyPrice + price) * size * DEFAULT_COMMISSION_RATE;
    const profit = grossProfit - fee;

    console.log(`[${this.name}] 📈 トレーリング利確: ${size} ${symbol} @ ¥${price.toLocaleString()} (利益: ¥${profit.toFixed(0)})`);

    if (!this.dryRun) {
      // 残高チェック
      try {
        const balances = await bitflyer.getBalance();
        const cryptoBalance = balances.find(b => b.currency_code === symbol)?.available || 0;
        if (cryptoBalance < size) {
          console.log(`[${this.name}] ⚠️ ${symbol}残高不足 - stateリセット`);
          this.state.position = 0;
          this.state.avgBuyPrice = 0;
          this.state.trailingActive = false;
          this.saveState();
          return;
        }
      } catch (e) {
        console.error(`[${this.name}] 残高取得失敗:`, e.message);
      }

      try {
        await bitflyer.sendOrder({
          product_code: this.pair,
          child_order_type: 'MARKET',
          side: 'SELL',
          size: size
        });
      } catch (error) {
        console.error(`[${this.name}] ❌ トレーリング注文失敗`);
        this.state.position = 0;
        this.state.avgBuyPrice = 0;
        this.state.trailingActive = false;
        this.saveState();
        return;
      }
    } else {
      console.log(`[${this.name}] (DRY RUN) トレーリング: ${size} @ ¥${price.toLocaleString()}`);
    }

    // 状態リセット
    this.state.totalProfit += profit;
    this.state.tradeCount++;
    this.state.position = 0;
    this.state.avgBuyPrice = 0;
    this.state.maxProfitPercent = 0;
    this.state.trailingActive = false;
    this.state.lastAction = 'TRAILING_STOP';
    this.state.lastActionTime = new Date().toISOString();
    this.saveState();

    await notify.notifyTrade(this.pair, 'SELL', price, size, profit);
    await notify.sendDiscord(`📈 **${this.name}** トレーリング利確！ +${profitPercent.toFixed(1)}% (最大+${this.state.maxProfitPercent.toFixed(1)}%) @ ¥${price.toLocaleString()} (利益: ¥${profit.toFixed(0)})`);
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
      maxProfitPercent: this.state.maxProfitPercent,
      trailingActive: this.state.trailingActive
    };
  }
}

module.exports = RSIStrategy;
