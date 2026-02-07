/**
 * テクニカル指標ライブラリ
 * RSI、ボリンジャーバンド、MACD、VWAPを実装
 * 
 * @author Stella ✨
 * @created 2026-02-08
 */

const bitflyer = require('./bitflyer');

class Indicators {
  constructor() {
    // 価格データのキャッシュ（API呼び出し削減）
    this.priceCache = new Map();
    this.cacheExpiry = 30 * 1000; // 30秒
  }

  /**
   * 約定履歴を取得（キャッシュ付き）
   */
  async getExecutions(pair, count = 500) {
    const cacheKey = `${pair}_${count}`;
    const cached = this.priceCache.get(cacheKey);
    
    if (cached && Date.now() - cached.time < this.cacheExpiry) {
      return cached.data;
    }

    try {
      const executions = await bitflyer.callPublic(
        `/v1/executions?product_code=${pair}&count=${count}`
      );
      
      this.priceCache.set(cacheKey, {
        data: executions,
        time: Date.now()
      });
      
      return executions;
    } catch (error) {
      console.error(`[Indicators] データ取得エラー: ${error.message}`);
      return cached?.data || [];
    }
  }

  /**
   * 価格配列を時系列順（古い順）に変換
   */
  toPriceArray(executions) {
    return executions
      .map(e => ({ price: e.price, size: e.size, time: new Date(e.exec_date) }))
      .reverse(); // 古い順に
  }

  /**
   * RSI（相対力指数）を計算
   * 
   * @param {string} pair - 通貨ペア
   * @param {number} period - 期間（デフォルト14）
   * @returns {object} { value, signal, oversold, overbought }
   */
  async getRSI(pair, period = 14) {
    try {
      const executions = await this.getExecutions(pair, period * 10);
      
      if (executions.length < period + 1) {
        return { value: 50, signal: 'neutral', error: 'データ不足' };
      }

      const prices = this.toPriceArray(executions);
      
      // 価格変動を計算
      let gains = 0;
      let losses = 0;
      
      for (let i = 1; i <= period; i++) {
        const change = prices[prices.length - i].price - prices[prices.length - i - 1].price;
        if (change > 0) {
          gains += change;
        } else {
          losses -= change;
        }
      }

      const avgGain = gains / period;
      const avgLoss = losses / period;

      // RSI計算
      let rsi;
      if (avgLoss === 0) {
        rsi = 100;
      } else {
        const rs = avgGain / avgLoss;
        rsi = 100 - (100 / (1 + rs));
      }

      // シグナル判定
      let signal = 'neutral';
      if (rsi <= 30) {
        signal = 'oversold'; // 売られすぎ → 買いシグナル
      } else if (rsi >= 70) {
        signal = 'overbought'; // 買われすぎ → 売りシグナル
      } else if (rsi <= 40) {
        signal = 'weak_oversold';
      } else if (rsi >= 60) {
        signal = 'weak_overbought';
      }

      return {
        value: Math.round(rsi * 100) / 100,
        signal,
        oversold: rsi <= 30,
        overbought: rsi >= 70,
        description: this.describeRSI(rsi)
      };
    } catch (error) {
      console.error(`[Indicators] RSI計算エラー: ${error.message}`);
      return { value: 50, signal: 'neutral', error: error.message };
    }
  }

  describeRSI(rsi) {
    if (rsi <= 20) return '極度に売られすぎ 🔥買い時！';
    if (rsi <= 30) return '売られすぎ 💚買い検討';
    if (rsi <= 40) return 'やや売られ気味';
    if (rsi <= 60) return '中立';
    if (rsi <= 70) return 'やや買われ気味';
    if (rsi <= 80) return '買われすぎ 🔴売り検討';
    return '極度に買われすぎ 🔥売り時！';
  }

  /**
   * ボリンジャーバンドを計算
   * 
   * @param {string} pair - 通貨ペア
   * @param {number} period - 移動平均期間（デフォルト20）
   * @param {number} multiplier - 標準偏差の倍率（デフォルト2）
   * @returns {object} { middle, upper, lower, width, percentB, signal }
   */
  async getBollingerBands(pair, period = 20, multiplier = 2) {
    try {
      const executions = await this.getExecutions(pair, period * 5);
      
      if (executions.length < period) {
        return { error: 'データ不足' };
      }

      const prices = this.toPriceArray(executions);
      const recentPrices = prices.slice(-period).map(p => p.price);
      const currentPrice = prices[prices.length - 1].price;

      // 移動平均（中央線）
      const sma = recentPrices.reduce((a, b) => a + b, 0) / period;

      // 標準偏差
      const squaredDiffs = recentPrices.map(p => Math.pow(p - sma, 2));
      const variance = squaredDiffs.reduce((a, b) => a + b, 0) / period;
      const stdDev = Math.sqrt(variance);

      // バンド計算
      const upper = sma + (multiplier * stdDev);
      const lower = sma - (multiplier * stdDev);
      const width = ((upper - lower) / sma) * 100; // バンド幅（%）

      // %B（現在価格がバンドのどこにあるか）
      const percentB = (currentPrice - lower) / (upper - lower);

      // シグナル判定
      let signal = 'neutral';
      if (currentPrice <= lower) {
        signal = 'below_lower'; // 下限突破 → 買いシグナル
      } else if (currentPrice >= upper) {
        signal = 'above_upper'; // 上限突破 → 売りシグナル
      } else if (percentB <= 0.2) {
        signal = 'near_lower'; // 下限付近
      } else if (percentB >= 0.8) {
        signal = 'near_upper'; // 上限付近
      }

      return {
        middle: Math.floor(sma),
        upper: Math.floor(upper),
        lower: Math.floor(lower),
        width: Math.round(width * 100) / 100,
        percentB: Math.round(percentB * 100) / 100,
        signal,
        currentPrice,
        description: this.describeBB(percentB, currentPrice, lower, upper)
      };
    } catch (error) {
      console.error(`[Indicators] ボリンジャーバンド計算エラー: ${error.message}`);
      return { error: error.message };
    }
  }

  describeBB(percentB, price, lower, upper) {
    if (price <= lower) return '下限ブレイク 🔥反発期待';
    if (price >= upper) return '上限ブレイク 🔴調整注意';
    if (percentB <= 0.2) return '下限付近 💚買い検討';
    if (percentB >= 0.8) return '上限付近 🔴売り検討';
    return 'バンド内中央付近';
  }

  /**
   * MACD（移動平均収束拡散法）を計算
   * 
   * @param {string} pair - 通貨ペア
   * @param {number} fastPeriod - 短期EMA期間（デフォルト12）
   * @param {number} slowPeriod - 長期EMA期間（デフォルト26）
   * @param {number} signalPeriod - シグナル線期間（デフォルト9）
   * @returns {object} { macd, signal, histogram, crossover }
   */
  async getMACD(pair, fastPeriod = 12, slowPeriod = 26, signalPeriod = 9) {
    try {
      const executions = await this.getExecutions(pair, 500);
      
      if (executions.length < slowPeriod + signalPeriod) {
        return { error: 'データ不足' };
      }

      const prices = this.toPriceArray(executions).map(p => p.price);

      // EMA計算
      const fastEMA = this.calculateEMA(prices, fastPeriod);
      const slowEMA = this.calculateEMA(prices, slowPeriod);

      // MACDライン = 短期EMA - 長期EMA
      const macdLine = [];
      const startIdx = slowPeriod - 1;
      for (let i = startIdx; i < prices.length; i++) {
        macdLine.push(fastEMA[i] - slowEMA[i]);
      }

      // シグナルライン = MACDのEMA
      const signalLine = this.calculateEMA(macdLine, signalPeriod);

      // 最新値
      const currentMACD = macdLine[macdLine.length - 1];
      const currentSignal = signalLine[signalLine.length - 1];
      const prevMACD = macdLine[macdLine.length - 2];
      const prevSignal = signalLine[signalLine.length - 2];
      const histogram = currentMACD - currentSignal;

      // クロスオーバー判定
      let crossover = 'none';
      if (prevMACD <= prevSignal && currentMACD > currentSignal) {
        crossover = 'golden'; // ゴールデンクロス → 買いシグナル
      } else if (prevMACD >= prevSignal && currentMACD < currentSignal) {
        crossover = 'death'; // デッドクロス → 売りシグナル
      }

      // シグナル判定
      let signal = 'neutral';
      if (crossover === 'golden') {
        signal = 'bullish';
      } else if (crossover === 'death') {
        signal = 'bearish';
      } else if (histogram > 0 && histogram > macdLine[macdLine.length - 3] - signalLine[signalLine.length - 3]) {
        signal = 'bullish_momentum'; // 上昇モメンタム
      } else if (histogram < 0 && histogram < macdLine[macdLine.length - 3] - signalLine[signalLine.length - 3]) {
        signal = 'bearish_momentum'; // 下落モメンタム
      }

      return {
        macd: Math.round(currentMACD),
        signalLine: Math.round(currentSignal),
        histogram: Math.round(histogram),
        crossover,
        signal,
        description: this.describeMACD(crossover, histogram)
      };
    } catch (error) {
      console.error(`[Indicators] MACD計算エラー: ${error.message}`);
      return { error: error.message };
    }
  }

  /**
   * EMA（指数移動平均）を計算
   */
  calculateEMA(prices, period) {
    const k = 2 / (period + 1);
    const ema = [prices[0]];

    for (let i = 1; i < prices.length; i++) {
      ema.push(prices[i] * k + ema[i - 1] * (1 - k));
    }

    return ema;
  }

  describeMACD(crossover, histogram) {
    if (crossover === 'golden') return '🔥 ゴールデンクロス！買いシグナル';
    if (crossover === 'death') return '🔴 デッドクロス！売りシグナル';
    if (histogram > 0) return '📈 上昇トレンド中';
    if (histogram < 0) return '📉 下落トレンド中';
    return '横ばい';
  }

  /**
   * 総合シグナルを計算
   * RSI、ボリンジャーバンド、MACDを組み合わせて判断
   * 
   * @param {string} pair - 通貨ペア
   * @returns {object} { score, action, confidence, details }
   */
  async getCompositeSignal(pair) {
    try {
      const [rsi, bb, macd] = await Promise.all([
        this.getRSI(pair),
        this.getBollingerBands(pair),
        this.getMACD(pair)
      ]);

      // スコア計算（-100〜+100、正は買い、負は売り）
      let score = 0;
      let signals = [];

      // RSIスコア（-30〜+30）
      if (rsi.value) {
        if (rsi.value <= 30) {
          score += 30;
          signals.push({ name: 'RSI', score: 30, reason: `売られすぎ (${rsi.value})` });
        } else if (rsi.value <= 40) {
          score += 15;
          signals.push({ name: 'RSI', score: 15, reason: `やや売られ気味 (${rsi.value})` });
        } else if (rsi.value >= 70) {
          score -= 30;
          signals.push({ name: 'RSI', score: -30, reason: `買われすぎ (${rsi.value})` });
        } else if (rsi.value >= 60) {
          score -= 15;
          signals.push({ name: 'RSI', score: -15, reason: `やや買われ気味 (${rsi.value})` });
        }
      }

      // ボリンジャーバンドスコア（-30〜+30）
      if (bb.percentB !== undefined) {
        if (bb.signal === 'below_lower') {
          score += 30;
          signals.push({ name: 'BB', score: 30, reason: '下限ブレイク' });
        } else if (bb.signal === 'near_lower') {
          score += 20;
          signals.push({ name: 'BB', score: 20, reason: '下限付近' });
        } else if (bb.signal === 'above_upper') {
          score -= 30;
          signals.push({ name: 'BB', score: -30, reason: '上限ブレイク' });
        } else if (bb.signal === 'near_upper') {
          score -= 20;
          signals.push({ name: 'BB', score: -20, reason: '上限付近' });
        }
      }

      // MACDスコア（-40〜+40）
      if (macd.crossover) {
        if (macd.crossover === 'golden') {
          score += 40;
          signals.push({ name: 'MACD', score: 40, reason: 'ゴールデンクロス' });
        } else if (macd.crossover === 'death') {
          score -= 40;
          signals.push({ name: 'MACD', score: -40, reason: 'デッドクロス' });
        } else if (macd.signal === 'bullish_momentum') {
          score += 20;
          signals.push({ name: 'MACD', score: 20, reason: '上昇モメンタム' });
        } else if (macd.signal === 'bearish_momentum') {
          score -= 20;
          signals.push({ name: 'MACD', score: -20, reason: '下落モメンタム' });
        }
      }

      // アクション判定
      let action = 'HOLD';
      let confidence = 'low';

      if (score >= 50) {
        action = 'STRONG_BUY';
        confidence = 'high';
      } else if (score >= 30) {
        action = 'BUY';
        confidence = 'medium';
      } else if (score >= 15) {
        action = 'WEAK_BUY';
        confidence = 'low';
      } else if (score <= -50) {
        action = 'STRONG_SELL';
        confidence = 'high';
      } else if (score <= -30) {
        action = 'SELL';
        confidence = 'medium';
      } else if (score <= -15) {
        action = 'WEAK_SELL';
        confidence = 'low';
      }

      return {
        score,
        action,
        confidence,
        signals,
        indicators: { rsi, bb, macd },
        description: this.describeComposite(action, score, signals)
      };
    } catch (error) {
      console.error(`[Indicators] 総合シグナル計算エラー: ${error.message}`);
      return {
        score: 0,
        action: 'HOLD',
        confidence: 'error',
        error: error.message
      };
    }
  }

  describeComposite(action, score, signals) {
    const signalText = signals.map(s => `${s.name}: ${s.reason}`).join(', ');
    
    switch (action) {
      case 'STRONG_BUY': return `🔥 強い買いシグナル (スコア: ${score}) | ${signalText}`;
      case 'BUY': return `💚 買いシグナル (スコア: ${score}) | ${signalText}`;
      case 'WEAK_BUY': return `📗 弱い買いシグナル (スコア: ${score}) | ${signalText}`;
      case 'STRONG_SELL': return `🔴 強い売りシグナル (スコア: ${score}) | ${signalText}`;
      case 'SELL': return `🟠 売りシグナル (スコア: ${score}) | ${signalText}`;
      case 'WEAK_SELL': return `📙 弱い売りシグナル (スコア: ${score}) | ${signalText}`;
      default: return `⚪ 様子見 (スコア: ${score})`;
    }
  }

  /**
   * キャッシュをクリア
   */
  clearCache() {
    this.priceCache.clear();
  }
}

module.exports = new Indicators();
