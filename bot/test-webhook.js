require('dotenv').config();
const axios = require('axios');

const webhookUrl = process.env.DISCORD_WEBHOOK_URL;

async function testWebhook() {
  if (!webhookUrl) {
    console.log('❌ DISCORD_WEBHOOK_URL が設定されていません');
    return;
  }

  try {
    await axios.post(webhookUrl, {
      content: '🤖 **Stella Trader** 通知テスト！\n\nWebhook接続成功 ✅'
    });
    console.log('✅ 通知送信成功！');
  } catch (error) {
    console.error('❌ エラー:', error.message);
  }
}

testWebhook();
