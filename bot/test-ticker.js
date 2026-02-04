const axios = require('axios');

const BASE_URL = 'https://api.bitflyer.com';

// 取得したい通貨ペア
const pairs = ['BTC_JPY', 'ETH_JPY', 'DOGE_JPY', 'SHIB_JPY', 'LINK_JPY', 'XYM_JPY'];

async function getTicker(productCode) {
  const response = await axios.get(`${BASE_URL}/v1/ticker?product_code=${productCode}`);
  return response.data;
}

async function getAllPrices() {
  console.log('=== 現在価格 ===\n');
  
  for (const pair of pairs) {
    try {
      const ticker = await getTicker(pair);
      const symbol = pair.replace('_JPY', '');
      console.log(`【${symbol}】`);
      console.log(`  現在値: ¥${ticker.ltp.toLocaleString()}`);
      console.log(`  買値:   ¥${ticker.best_bid.toLocaleString()}`);
      console.log(`  売値:   ¥${ticker.best_ask.toLocaleString()}`);
      console.log(`  24h出来高: ${ticker.volume_by_product.toLocaleString()}`);
      console.log('');
    } catch (error) {
      console.log(`【${pair}】取得エラー: ${error.response?.data?.error_message || error.message}\n`);
    }
  }
}

getAllPrices();
