require('dotenv').config();
const crypto = require('crypto');
const axios = require('axios');

const API_KEY = process.env.BITFLYER_API_KEY;
const API_SECRET = process.env.BITFLYER_API_SECRET;
const BASE_URL = 'https://api.bitflyer.com';

async function callPrivateAPI(method, path, body = '') {
  const timestamp = Date.now().toString();
  const text = timestamp + method + path + body;
  const sign = crypto.createHmac('sha256', API_SECRET).update(text).digest('hex');

  const response = await axios({
    method,
    url: BASE_URL + path,
    headers: {
      'ACCESS-KEY': API_KEY,
      'ACCESS-TIMESTAMP': timestamp,
      'ACCESS-SIGN': sign,
      'Content-Type': 'application/json'
    },
    data: body || undefined
  });

  return response.data;
}

async function getBalance() {
  try {
    const balance = await callPrivateAPI('GET', '/v1/me/getbalance');
    console.log('=== 残高 ===');
    balance.forEach(b => {
      if (b.amount > 0) {
        console.log(`${b.currency_code}: ${b.amount} (利用可能: ${b.available})`);
      }
    });
  } catch (error) {
    console.error('エラー:', error.response?.data || error.message);
  }
}

getBalance();
