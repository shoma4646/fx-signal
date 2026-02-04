const fs = require('fs');
const path = require('path');
const notify = require('./notify');

const SAFETY_STATE_FILE = path.join(__dirname, '../data/safety-state.json');

class Safety {
  constructor() {
    this.state = this.loadState();
  }

  loadState() {
    if (fs.existsSync(SAFETY_STATE_FILE)) {
      return JSON.parse(fs.readFileSync(SAFETY_STATE_FILE, 'utf8'));
    }
    return this.defaultState();
  }

  defaultState() {
    return {
      priceHistory: {},      // 価格履歴（急変動検知用）
      dailyLoss: 0,          // 今日の損失
      dailyProfit: 0,        // 今日の利益
      lastResetDate: new Date().toISOString().split('T')[0],
      isPaused: false,       // 緊急停止中か
      pauseReason: null,
      pausedAt: null
    };
  }

  saveState() {
    fs.writeFileSync(SAFETY_STATE_FILE, JSON.stringify(this.state, null, 2));
  }

  // 日次リセット
  checkDailyReset() {
    const today = new Date().toISOString().split('T')[0];
    if (this.state.lastResetDate !== today) {
      this.state.dailyLoss = 0;
      this.state.dailyProfit = 0;
      this.state.lastResetDate = today;
      // 日が変わったら自動復帰（手動停止以外）
      if (this.state.pauseReason !== 'manual') {
        this.state.isPaused = false;
        this.state.pauseReason = null;
      }
      this.saveState();
    }
  }

  // 急変動チェック（5分で2%以上）
  async checkVolatility(pair, currentPrice, thresholdPercent = 2) {
    const now = Date.now();
    const fiveMinAgo = now - 5 * 60 * 1000;

    if (!this.state.priceHistory[pair]) {
      this.state.priceHistory[pair] = [];
    }

    // 古いデータを削除
    this.state.priceHistory[pair] = this.state.priceHistory[pair]
      .filter(p => p.time > fiveMinAgo);

    // 現在価格を追加
    this.state.priceHistory[pair].push({ price: currentPrice, time: now });

    // 5分前の価格と比較
    const oldPrices = this.state.priceHistory[pair].filter(p => p.time <= now - 4 * 60 * 1000);
    if (oldPrices.length === 0) {
      this.saveState();
      return { safe: true };
    }

    const oldPrice = oldPrices[0].price;
    const changePercent = ((currentPrice - oldPrice) / oldPrice) * 100;

    if (Math.abs(changePercent) >= thresholdPercent) {
      this.state.isPaused = true;
      this.state.pauseReason = 'volatility';
      this.state.pausedAt = new Date().toISOString();
      this.saveState();

      const direction = changePercent > 0 ? '急騰' : '急落';
      await notify.sendDiscord(
        `🚨 **${pair} ${direction}検知！** (${changePercent.toFixed(1)}%/5分)\n` +
        `Bot を一時停止しました。様子を見て手動で再開してください。`
      );

      return { safe: false, reason: 'volatility', change: changePercent };
    }

    this.saveState();
    return { safe: true };
  }

  // 損失記録 & 上限チェック
  async recordTrade(profit, maxDailyLoss = 5000) {
    this.checkDailyReset();

    if (profit < 0) {
      this.state.dailyLoss += Math.abs(profit);
    } else {
      this.state.dailyProfit += profit;
    }

    this.saveState();

    // 日次損失上限チェック
    if (this.state.dailyLoss >= maxDailyLoss) {
      this.state.isPaused = true;
      this.state.pauseReason = 'daily_loss_limit';
      this.state.pausedAt = new Date().toISOString();
      this.saveState();

      await notify.sendDiscord(
        `🛑 **日次損失上限到達！** (¥${this.state.dailyLoss.toLocaleString()})\n` +
        `本日のBot取引を停止しました。明日自動再開します。`
      );

      return { ok: false, reason: 'daily_loss_limit' };
    }

    return { ok: true };
  }

  // Bot実行可能かチェック
  canTrade() {
    this.checkDailyReset();
    return !this.state.isPaused;
  }

  // 手動で再開
  resume() {
    this.state.isPaused = false;
    this.state.pauseReason = null;
    this.state.pausedAt = null;
    this.saveState();
  }

  // 手動で停止
  pause(reason = 'manual') {
    this.state.isPaused = true;
    this.state.pauseReason = reason;
    this.state.pausedAt = new Date().toISOString();
    this.saveState();
  }

  getStatus() {
    this.checkDailyReset();
    return {
      isPaused: this.state.isPaused,
      pauseReason: this.state.pauseReason,
      dailyLoss: this.state.dailyLoss,
      dailyProfit: this.state.dailyProfit,
      netPnL: this.state.dailyProfit - this.state.dailyLoss
    };
  }
}

module.exports = new Safety();
