require('dotenv').config();
const crypto = require('crypto');
const axios = require('axios');

const API_KEY = process.env.BITFLYER_API_KEY;
const API_SECRET = process.env.BITFLYER_API_SECRET;
const BASE_URL = 'https://api.bitflyer.com';

class BitFlyer {
  async callPrivate(method, path, body = null) {
    const timestamp = Date.now().toString();
    const bodyStr = body ? JSON.stringify(body) : '';
    const text = timestamp + method + path + bodyStr;
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

  async callPublic(path) {
    const response = await axios.get(BASE_URL + path);
    return response.data;
  }

  // Public APIs
  async getTicker(productCode) {
    return this.callPublic(`/v1/ticker?product_code=${productCode}`);
  }

  async getBoard(productCode) {
    return this.callPublic(`/v1/board?product_code=${productCode}`);
  }

  // Private APIs
  async getBalance() {
    return this.callPrivate('GET', '/v1/me/getbalance');
  }

  async getOrders(productCode, state = 'ACTIVE') {
    return this.callPrivate('GET', `/v1/me/getchildorders?product_code=${productCode}&child_order_state=${state}`);
  }

  async getExecutions(productCode, count = 100) {
    return this.callPrivate('GET', `/v1/me/getexecutions?product_code=${productCode}&count=${count}`);
  }

  async sendOrder(params) {
    // params: { product_code, child_order_type, side, price, size }
    try {
      const result = await this.callPrivate('POST', '/v1/me/sendchildorder', params);
      console.log(`[bitFlyer] ✅ 注文成功: ${params.side} ${params.size} ${params.product_code}`);
      return result;
    } catch (error) {
      const errMsg = error.response?.data || error.message;
      console.error(`[bitFlyer] ❌ 注文失敗: ${params.side} ${params.size} ${params.product_code}`);
      console.error(`[bitFlyer]    詳細: ${JSON.stringify(errMsg)}`);
      throw error;
    }
  }

  async cancelOrder(productCode, childOrderId) {
    return this.callPrivate('POST', '/v1/me/cancelchildorder', {
      product_code: productCode,
      child_order_id: childOrderId
    });
  }

  async cancelAllOrders(productCode) {
    return this.callPrivate('POST', '/v1/me/cancelallchildorders', {
      product_code: productCode
    });
  }

  // 取引手数料率を取得
  async getTradingCommission(productCode) {
    try {
      const result = await this.callPrivate('GET', `/v1/me/gettradingcommission?product_code=${productCode}`);
      return result.commission_rate || 0.0015; // デフォルト0.15%
    } catch (error) {
      console.error('[bitFlyer] 手数料取得エラー:', error.message);
      return 0.0015; // エラー時はデフォルト0.15%
    }
  }
}

module.exports = new BitFlyer();
