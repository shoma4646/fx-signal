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
    return this.callPrivate('POST', '/v1/me/sendchildorder', params);
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
}

module.exports = new BitFlyer();
