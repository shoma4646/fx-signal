const axios = require('axios');

const BASE_URL = 'https://api.bitflyer.com';
const pairs = ['BTC_JPY', 'ETH_JPY', 'XRP_JPY', 'XLM_JPY', 'MONA_JPY'];

async function checkLiquidity() {
  console.log('=== 流動性チェック ===\n');
  console.log('通貨\t\t現在価格\t\tスプレッド\t24h出来高\t評価');
  console.log('─'.repeat(80));

  for (const pair of pairs) {
    try {
      const res = await axios.get(`${BASE_URL}/v1/ticker?product_code=${pair}`);
      const t = res.data;
      
      const spread = t.best_ask - t.best_bid;
      const spreadPercent = (spread / t.ltp * 100).toFixed(3);
      const symbol = pair.replace('_JPY', '').padEnd(4);
      
      // 評価基準
      let rating = '';
      if (t.volume_by_product > 100 && parseFloat(spreadPercent) < 0.1) {
        rating = '◎ 最適';
      } else if (t.volume_by_product > 10 && parseFloat(spreadPercent) < 0.5) {
        rating = '○ 良好';
      } else if (t.volume_by_product > 1) {
        rating = '△ 注意';
      } else {
        rating = '✕ 非推奨';
      }

      console.log(`${symbol}\t\t¥${t.ltp.toLocaleString().padStart(12)}\t${spreadPercent}%\t\t${t.volume_by_product.toFixed(1).padStart(10)}\t${rating}`);
    } catch (e) {
      console.log(`${pair}: エラー`);
    }
  }
  
  console.log('\n※ スプレッド = 売値 - 買値（小さいほど良い）');
  console.log('※ 出来高 = 24時間の取引量（多いほど良い）');
}

checkLiquidity();
