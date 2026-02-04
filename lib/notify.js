const fs = require('fs');
const path = require('path');
const axios = require('axios');

const LOG_DIR = path.join(__dirname, '../data');
const TRADE_LOG = path.join(LOG_DIR, 'trades.json');

class Notify {
  constructor() {
    this.webhookUrl = process.env.DISCORD_WEBHOOK_URL;
    this.ensureLogDir();
  }

  ensureLogDir() {
    if (!fs.existsSync(LOG_DIR)) {
      fs.mkdirSync(LOG_DIR, { recursive: true });
    }
  }

  // ログファイルに記録
  logTrade(trade) {
    let trades = [];
    if (fs.existsSync(TRADE_LOG)) {
      trades = JSON.parse(fs.readFileSync(TRADE_LOG, 'utf8'));
    }
    trades.push({
      ...trade,
      timestamp: new Date().toISOString()
    });
    fs.writeFileSync(TRADE_LOG, JSON.stringify(trades, null, 2));
  }

  // Discord Webhook送信
  async sendDiscord(message, embed = null) {
    if (!this.webhookUrl) {
      console.log('[Discord通知スキップ] Webhook未設定');
      return;
    }

    try {
      const payload = { content: message };
      if (embed) payload.embeds = [embed];
      await axios.post(this.webhookUrl, payload);
    } catch (error) {
      console.error('[Discord通知エラー]', error.message);
    }
  }

  // 約定通知
  async notifyTrade(pair, side, price, size, profit = null) {
    const emoji = side === 'BUY' ? '🟢' : '🔴';
    const action = side === 'BUY' ? '買い' : '売り';
    const symbol = pair.replace('_JPY', '');
    
    let message = `${emoji} **${symbol} ${action}** ${size} @ ¥${price.toLocaleString()}`;
    if (profit !== null) {
      const profitEmoji = profit >= 0 ? '📈' : '📉';
      message += `\n${profitEmoji} 損益: ¥${profit.toLocaleString()}`;
    }

    // ログに記録
    this.logTrade({ pair, side, price, size, profit });
    
    // Discord通知
    await this.sendDiscord(message);
    
    console.log(message);
  }

  // エラー通知
  async notifyError(error) {
    const message = `⚠️ **Botエラー**\n\`\`\`${error}\`\`\``;
    await this.sendDiscord(message);
    console.error('[ERROR]', error);
  }

  // 日次レポート
  async sendDailyReport(stats) {
    const embed = {
      title: '📊 日次レポート',
      color: stats.totalProfit >= 0 ? 0x00ff00 : 0xff0000,
      fields: [
        { name: '取引回数', value: `${stats.tradeCount}回`, inline: true },
        { name: '損益', value: `¥${stats.totalProfit.toLocaleString()}`, inline: true },
        { name: '勝率', value: `${stats.winRate.toFixed(1)}%`, inline: true },
        { name: '保有ポジション', value: stats.positions, inline: false }
      ],
      timestamp: new Date().toISOString()
    };

    await this.sendDiscord('', embed);
  }
}

module.exports = new Notify();
