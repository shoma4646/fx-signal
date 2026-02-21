/**
 * テクニカル指標ライブラリ
 * RSI、ボリンジャーバンド、MACD、VWAPを実装
 * 
 * @author Stella ✨
 * @created 2026-02-08
 * @updated 2026-02-14 - RSIを1時間足ベースに修正
 */

const bitflyer = require('./bitflyer');
const axios = require('axios');

class Indicators {
  constructor() {
    // 価格データのキャッシュ（API呼び出し削減）
    this.priceCache = new Map();
    this.ohlcCache = new Map();
    this.cacheExpiry = 30 * 1000; // 30秒
    this.ohlcCacheExpiry = 5 * 60 * 1000; // 5分（1時間足なので長めに）
  }

  /**
   * CryptoCompareから1時間足OHLCデータを取得
   */
  async getOHLCData(symbol, limit = 50) {
    const cacheKey = `ohlc_${symbol}_${limit}`;
    const cached = this.ohlcCache.get(cacheKey);
    
    if (cached && Date.now() - cached.time < this.ohlcCacheExpiry) {
      return cached.data;
    }

    try {
      // ETH_JPY → ETH, JPY に分解
      const [fsym, tsym] = symbol.includes('_') ? symbol.split('_') : [symbol, 'JPY'];
      
      const response = await axios.get(
        `https://min-api.cryptocompare.com/data/v2/histohour?fsym=${fsym}&tsym=${tsym}&limit=${limit}`
      );
      
      if (response.data.Response === 'Success') {
        const data = response.data.Data.Data;
        this.ohlcCache.set(cacheKey, {
          data,
          time: Date.now()
        });
        return data;
      }
      
      throw new Error('CryptoCompare API error');
    } catch (error) {
      console.error(`[Indicators] OHLC取得エラー: ${error.message}`);
      return cached?.data || [];
    }
  }

  /**
   * 約定履歴を取得（キャッシュ付き）- BB/MACD用
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
   * RSI（相対力指数）を計算 - 1時間足ベース
   * 
   * @param {string} pair - 通貨ペア
   * @param {number} period - 期間（デフォルト14）
   * @returns {object} { value, signal, oversold, overbought }
   */
  async getRSI(pair, period = 14) {
    try {
      // 1時間足データを取得
      const ohlcData = await this.getOHLCData(pair, period + 10);
      
      if (ohlcData.length < period + 1) {
        return { value: 50, signal: 'neutral', error: 'データ不足' };
      }

      // 終値を抽出
      const closes = ohlcData.map(d => d.close);
      
      // 価格変動を計算（Wilder's smoothing method）
      let gains = 0;
      let losses = 0;
      
      // 最初のperiod期間の平均を計算
      for (let i = 1; i <= period; i++) {
        const change = closes[closes.length - i] - closes[closes.length - i - 1];
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
        signal = 'oversold';
      } else if (rsi >= 70) {
        signal = 'overbought';
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
        description: this.describeRSI(rsi),
        timeframe: '1h'
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
   * ボリンジャーバンドを計算 - 1時間足ベース
   */
  async getBollingerBands(pair, period = 20, multiplier = 2) {
    try {
      const ohlcData = await this.getOHLCData(pair, period + 5);
      
      if (ohlcData.length < period) {
        return { error: 'データ不足' };
      }

      const closes = ohlcData.map(d => d.close);
      const recentPrices = closes.slice(-period);
      const currentPrice = closes[closes.length - 1];

      // 移動平均（中央線）
      const sma = recentPrices.reduce((a, b) => a + b, 0) / period;

      // 標準偏差
      const squaredDiffs = recentPrices.map(p => Math.pow(p - sma, 2));
      const variance = squaredDiffs.reduce((a, b) => a + b, 0) / period;
      const stdDev = Math.sqrt(variance);

      // バンド計算
      const upper = sma + (multiplier * stdDev);
      const lower = sma - (multiplier * stdDev);
      const width = ((upper - lower) / sma) * 100;

      // %B（現在価格がバンドのどこにあるか）
      const percentB = (currentPrice - lower) / (upper - lower);

      // シグナル判定
      let signal = 'neutral';
      if (currentPrice <= lower) {
        signal = 'below_lower';
      } else if (currentPrice >= upper) {
        signal = 'above_upper';
      } else if (percentB <= 0.2) {
        signal = 'near_lower';
      } else if (percentB >= 0.8) {
        signal = 'near_upper';
      }

      return {
        middle: Math.floor(sma),
        upper: Math.floor(upper),
        lower: Math.floor(lower),
        width: Math.round(width * 100) / 100,
        percentB: Math.round(percentB * 100) / 100,
        signal,
        currentPrice,
        description: this.describeBB(percentB, currentPrice, lower, upper),
        timeframe: '1h'
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
   * MACD（移動平均収束拡散法）を計算 - 1時間足ベース
   */
  async getMACD(pair, fastPeriod = 12, slowPeriod = 26, signalPeriod = 9) {
    try {
      const ohlcData = await this.getOHLCData(pair, slowPeriod + signalPeriod + 10);
      
      if (ohlcData.length < slowPeriod + signalPeriod) {
        return { error: 'データ不足' };
      }

      const prices = ohlcData.map(d => d.close);

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
        crossover = 'golden';
      } else if (prevMACD >= prevSignal && currentMACD < currentSignal) {
        crossover = 'death';
      }

      // トレンド判定
      let trend = 'neutral';
      if (histogram > 0) {
        trend = 'bullish';
      } else if (histogram < 0) {
        trend = 'bearish';
      }

      return {
        macd: Math.round(currentMACD),
        signal: Math.round(currentSignal),
        histogram: Math.round(histogram),
        crossover,
        trend,
        description: this.describeMACD(crossover, histogram),
        timeframe: '1h'
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
   * 複合スコアリング - RSI、BB、MACDを統合して-100〜+100のスコアを返す
   * 
   * @param {string} pair - 通貨ペア
   * @returns {object} { score, signal, components, description }
   */
  async getCompositeScore(pair) {
    try {
      // 各指標を並列で取得
      const [rsi, bb, macd] = await Promise.all([
        this.getRSI(pair),
        this.getBollingerBands(pair),
        this.getMACD(pair)
      ]);

      // エラーチェック
      if (rsi.error && bb.error && macd.error) {
        return { 
          score: 0, 
          signal: 'neutral', 
          error: 'すべての指標でエラー',
          components: { rsi, bb, macd }
        };
      }

      let totalScore = 0;
      const breakdown = [];

      // ===== RSI スコア計算 (最大±30点) =====
      let rsiScore = 0;
      let rsiReason = '';
      if (!rsi.error) {
        const v = rsi.value;
        if (v <= 20) {
          rsiScore = 30;
          rsiReason = `極度の売られすぎ (${v.toFixed(1)})`;
        } else if (v <= 30) {
          rsiScore = 25;
          rsiReason = `売られすぎ (${v.toFixed(1)})`;
        } else if (v <= 40) {
          rsiScore = 10;
          rsiReason = `やや売られ気味 (${v.toFixed(1)})`;
        } else if (v >= 80) {
          rsiScore = -30;
          rsiReason = `極度の買われすぎ (${v.toFixed(1)})`;
        } else if (v >= 70) {
          rsiScore = -25;
          rsiReason = `買われすぎ (${v.toFixed(1)})`;
        } else if (v >= 60) {
          rsiScore = -10;
          rsiReason = `やや買われ気味 (${v.toFixed(1)})`;
        } else {
          rsiScore = 0;
          rsiReason = `中立 (${v.toFixed(1)})`;
        }
        totalScore += rsiScore;
        breakdown.push({ name: 'RSI', score: rsiScore, reason: rsiReason });
      }

      // ===== ボリンジャーバンド スコア計算 (最大±25点) =====
      let bbScore = 0;
      let bbReason = '';
      if (!bb.error) {
        const pB = bb.percentB;
        if (pB <= 0) {
          bbScore = 25;
          bbReason = `下限ブレイク (%B: ${(pB * 100).toFixed(1)}%)`;
        } else if (pB <= 0.1) {
          bbScore = 20;
          bbReason = `下限付近 (%B: ${(pB * 100).toFixed(1)}%)`;
        } else if (pB <= 0.2) {
          bbScore = 15;
          bbReason = `やや下限寄り (%B: ${(pB * 100).toFixed(1)}%)`;
        } else if (pB >= 1.0) {
          bbScore = -25;
          bbReason = `上限ブレイク (%B: ${(pB * 100).toFixed(1)}%)`;
        } else if (pB >= 0.9) {
          bbScore = -20;
          bbReason = `上限付近 (%B: ${(pB * 100).toFixed(1)}%)`;
        } else if (pB >= 0.8) {
          bbScore = -15;
          bbReason = `やや上限寄り (%B: ${(pB * 100).toFixed(1)}%)`;
        } else {
          bbScore = 0;
          bbReason = `バンド内中央 (%B: ${(pB * 100).toFixed(1)}%)`;
        }
        totalScore += bbScore;
        breakdown.push({ name: 'BB', score: bbScore, reason: bbReason });
      }

      // ===== MACD スコア計算 (クロス±25点 + ヒストグラム±10点 = 最大±35点) =====
      let macdScore = 0;
      let macdReason = '';
      if (!macd.error) {
        // クロスオーバー判定
        if (macd.crossover === 'golden') {
          macdScore += 25;
          macdReason = 'ゴールデンクロス';
        } else if (macd.crossover === 'death') {
          macdScore -= 25;
          macdReason = 'デッドクロス';
        }

        // ヒストグラムのトレンド（勢い）
        if (macd.histogram > 0) {
          macdScore += Math.min(10, Math.abs(macd.histogram) / 100);
          macdReason += macdReason ? ' + 上昇勢い' : '上昇トレンド';
        } else if (macd.histogram < 0) {
          macdScore -= Math.min(10, Math.abs(macd.histogram) / 100);
          macdReason += macdReason ? ' + 下落勢い' : '下落トレンド';
        }

        if (!macdReason) macdReason = '中立';
        macdScore = Math.round(macdScore);
        totalScore += macdScore;
        breakdown.push({ name: 'MACD', score: macdScore, reason: macdReason });
      }

      // ===== 合計スコアを-100〜+100に正規化 =====
      totalScore = Math.max(-100, Math.min(100, Math.round(totalScore)));

      // ===== シグナル判定 =====
      let signal;
      if (totalScore >= 50) {
        signal = 'strong_buy';
      } else if (totalScore >= 20) {
        signal = 'buy';
      } else if (totalScore <= -50) {
        signal = 'strong_sell';
      } else if (totalScore <= -20) {
        signal = 'sell';
      } else {
        signal = 'neutral';
      }

      // ===== 人間向け説明文生成 =====
      const description = this.describeCompositeScore(totalScore, signal, breakdown);

      return {
        score: totalScore,
        signal,
        components: {
          rsi: { value: rsi.value, score: rsiScore, signal: rsi.signal },
          bb: { percentB: bb.percentB, score: bbScore, signal: bb.signal },
          macd: { histogram: macd.histogram, crossover: macd.crossover, score: macdScore }
        },
        breakdown,
        description,
        timeframe: '1h'
      };
    } catch (error) {
      console.error(`[Indicators] 複合スコア計算エラー: ${error.message}`);
      return { score: 0, signal: 'neutral', error: error.message };
    }
  }

  describeCompositeScore(score, signal, breakdown) {
    const signalEmoji = {
      strong_buy: '🔥🔥',
      buy: '💚',
      neutral: '➖',
      sell: '🔴',
      strong_sell: '💀💀'
    };

    const signalText = {
      strong_buy: '強い買いシグナル！',
      buy: '買い検討',
      neutral: '様子見推奨',
      sell: '売り検討',
      strong_sell: '強い売りシグナル！'
    };

    const emoji = signalEmoji[signal] || '➖';
    const text = signalText[signal] || '判定不能';
    const details = breakdown.map(b => `${b.name}:${b.score > 0 ? '+' : ''}${b.score}`).join(' / ');

    return `${emoji} ${text} (スコア: ${score}) [${details}]`;
  }

  /**
   * getCompositeSignal - getCompositeScoreのエイリアス（互換性用）
   */
  async getCompositeSignal(pair) {
    const result = await this.getCompositeScore(pair);
    // 旧形式との互換性のため、追加フィールドを付与
    return {
      ...result,
      action: result.signal,
      confidence: Math.abs(result.score) >= 50 ? 'high' : Math.abs(result.score) >= 20 ? 'medium' : 'low',
      signals: result.breakdown
    };
  }

  /**
   * キャッシュをクリア
   */
  clearCache() {
    this.priceCache.clear();
    this.ohlcCache.clear();
  }
}

module.exports = new Indicators();
