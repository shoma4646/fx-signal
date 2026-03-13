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

  // ステラにメンションして妥当性チェックを依頼
  async requestValidation(strategyName, pair, side, price, size, context = {}) {
    const STELLA_ID = '1464669395543654430';
    const symbol = pair.replace('_JPY', '');
    const action = side === 'BUY' ? '買い' : (side === 'SELL' ? '売り' : side);
    
    let message = `<@${STELLA_ID}> **ポジション妥当性チェック依頼**\n`;
    message += `📊 **${strategyName}** が ${symbol} を${action}しました\n`;
    message += `- 価格: ¥${price.toLocaleString()}\n`;
    message += `- 数量: ${size}\n`;
    
    if (context.position !== undefined) {
      message += `- 現在ポジション: ${context.position}\n`;
    }
    if (context.avgPrice) {
      message += `- 平均取得価格: ¥${context.avgPrice.toLocaleString()}\n`;
    }
    if (context.jpyBalance !== undefined) {
      message += `- JPY残高: ¥${context.jpyBalance.toLocaleString()}\n`;
    }
    if (context.cryptoBalance !== undefined) {
      message += `- ${symbol}残高: ${context.cryptoBalance}\n`;
    }
    
    message += `\n妥当性を確認してください 🙏`;
    
    await this.sendDiscord(message);
    console.log(`[Notify] ステラに妥当性チェック依頼送信: ${strategyName} ${action}`);
  }

  // 残高不足エラー通知
  async notifyInsufficientBalance(strategyName, pair, side, required, available) {
    const STELLA_ID = '1464669395543654430';
    const symbol = pair.replace('_JPY', '');
    
    let message = `<@${STELLA_ID}> ⚠️ **残高不足で注文スキップ**\n`;
    message += `- 戦略: ${strategyName}\n`;
    message += `- 注文: ${side} ${symbol}\n`;
    message += `- 必要: ${typeof required === 'number' ? required.toLocaleString() : required}\n`;
    message += `- 利用可能: ${typeof available === 'number' ? available.toLocaleString() : available}\n`;
    
    await this.sendDiscord(message);
    console.log(`[Notify] 残高不足通知: ${strategyName}`);
  }

  // 下落トレンドでbot停止通知
  async notifyBotStopped(reason, trendInfo) {
    const STELLA_ID = '1464669395543654430';
    
    let message = `<@${STELLA_ID}> 🛑 **Bot自動停止**\n`;
    message += `- 理由: ${reason}\n`;
    message += `- トレンド: ${trendInfo.trend} (${trendInfo.reason})\n`;
    message += `\nトレンドが転換したら再開をお願いします 🙏`;
    
    await this.sendDiscord(message);
    console.log(`[Notify] Bot停止通知: ${reason}`);
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
