const bitflyer = require('./bitflyer');
const indicators = require('./indicators');

class Analysis {
  // 過去の約定履歴から移動平均を計算
  async getMovingAverage(pair, hours = 24) {
    try {
      // 約定履歴を取得（最大500件）
      const executions = await bitflyer.callPublic(
        `/v1/executions?product_code=${pair}&count=500`
      );

      if (!executions || executions.length === 0) {
        return null;
      }

      // 指定時間内のデータをフィルタ
      const now = new Date();
      const cutoff = new Date(now.getTime() - hours * 60 * 60 * 1000);
      
      const recentExecutions = executions.filter(e => {
        const execDate = new Date(e.exec_date);
        return execDate >= cutoff;
      });

      if (recentExecutions.length === 0) {
        // 時間内のデータがなければ全データの平均
        const sum = executions.reduce((acc, e) => acc + e.price, 0);
        return Math.floor(sum / executions.length);
      }

      // 平均価格を計算
      const sum = recentExecutions.reduce((acc, e) => acc + e.price, 0);
      const avg = sum / recentExecutions.length;

      return Math.floor(avg);
    } catch (error) {
      console.error(`[Analysis] 移動平均計算エラー: ${error.message}`);
      return null;
    }
  }

  // 現在価格と基準価格の乖離率を計算
  calculateDeviation(currentPrice, basePrice) {
    if (!basePrice || basePrice === 0) return 0;
    return ((currentPrice - basePrice) / basePrice) * 100;
  }

  // ボラティリティ（価格変動幅）を計算
  async getVolatility(pair, hours = 24) {
    try {
      const executions = await bitflyer.callPublic(
        `/v1/executions?product_code=${pair}&count=500`
      );

      if (!executions || executions.length < 10) {
        return null;
      }

      const prices = executions.map(e => e.price);
      const high = Math.max(...prices);
      const low = Math.min(...prices);
      const avg = prices.reduce((a, b) => a + b, 0) / prices.length;

      return {
        high,
        low,
        range: high - low,
        rangePercent: ((high - low) / avg) * 100,
        avg: Math.floor(avg)
      };
    } catch (error) {
      console.error(`[Analysis] ボラティリティ計算エラー: ${error.message}`);
      return null;
    }
  }

  // トレンド判断（移動平均との比較）
  async getTrend(pair, currentPrice) {
    try {
      const ma24h = await this.getMovingAverage(pair, 24);
      
      if (!ma24h) {
        return { trend: 'neutral', reason: 'データ不足' };
      }

      const deviation = this.calculateDeviation(currentPrice, ma24h);
      
      // 1%以上乖離でトレンド判断
      if (deviation > 1.0) {
        return { 
          trend: 'bullish', 
          ma: ma24h, 
          deviation,
          reason: `価格がMA24hより${deviation.toFixed(1)}%上 → 上昇トレンド`
        };
      } else if (deviation < -1.0) {
        return { 
          trend: 'bearish', 
          ma: ma24h, 
          deviation,
          reason: `価格がMA24hより${Math.abs(deviation).toFixed(1)}%下 → 下落トレンド`
        };
      } else {
        return { 
          trend: 'neutral', 
          ma: ma24h, 
          deviation,
          reason: `MA24h付近でレンジ（乖離${deviation.toFixed(1)}%）`
        };
      }
    } catch (error) {
      console.error(`[Analysis] トレンド判断エラー: ${error.message}`);
      return { trend: 'neutral', reason: 'エラー' };
    }
  }

  // グリッド幅の推奨値を計算
  async recommendGridSpacing(pair) {
    const volatility = await this.getVolatility(pair, 24);
    
    if (!volatility) {
      return { spacing: 1.0, reason: 'デフォルト（データ不足）' };
    }

    // ボラティリティに基づいて推奨
    const rangePercent = volatility.rangePercent;

    if (rangePercent < 2) {
      return { spacing: 0.3, reason: `低ボラ（${rangePercent.toFixed(1)}%）→ 狭めグリッド` };
    } else if (rangePercent < 5) {
      return { spacing: 0.5, reason: `中ボラ（${rangePercent.toFixed(1)}%）→ 標準グリッド` };
    } else if (rangePercent < 10) {
      return { spacing: 1.0, reason: `高ボラ（${rangePercent.toFixed(1)}%）→ 広めグリッド` };
    } else {
      return { spacing: 1.5, reason: `超高ボラ（${rangePercent.toFixed(1)}%）→ 超広グリッド` };
    }
  }

  // ========================================
  // テクニカル指標（RSI、BB、MACD）
  // ========================================

  /**
   * RSI（相対力指数）を取得
   */
  async getRSI(pair, period = 14) {
    return indicators.getRSI(pair, period);
  }

  /**
   * ボリンジャーバンドを取得
   */
  async getBollingerBands(pair, period = 20, multiplier = 2) {
    return indicators.getBollingerBands(pair, period, multiplier);
  }

  /**
   * MACDを取得
   */
  async getMACD(pair) {
    return indicators.getMACD(pair);
  }

  /**
   * 総合シグナルを取得（RSI + BB + MACD）
   */
  async getCompositeSignal(pair) {
    return indicators.getCompositeSignal(pair);
  }

  /**
   * 拡張トレンド判断（既存トレンド + テクニカル指標）
   * 
   * @param {string} pair - 通貨ペア
   * @param {number} currentPrice - 現在価格
   * @returns {object} トレンド情報 + 取引推奨
   */
  async getEnhancedTrend(pair, currentPrice) {
    try {
      // 基本トレンド判断
      const basicTrend = await this.getTrend(pair, currentPrice);
      
      // テクニカル指標
      const composite = await this.getCompositeSignal(pair);

      // 最終判断
      let finalTrend = basicTrend.trend;
      let shouldTrade = true;
      let tradeAction = 'HOLD';
      let reason = basicTrend.reason;

      // 指標スコアが強い場合は上書き
      if (composite.confidence === 'high') {
        if (composite.action === 'STRONG_BUY' || composite.action === 'BUY') {
          if (basicTrend.trend === 'bearish') {
            // 下落トレンドだが指標は買いシグナル → 反転期待
            finalTrend = 'reversal_up';
            shouldTrade = true;
            tradeAction = 'BUY';
            reason = `${basicTrend.reason} だが ${composite.description}`;
          } else {
            finalTrend = 'bullish';
            shouldTrade = true;
            tradeAction = 'BUY';
            reason = composite.description;
          }
        } else if (composite.action === 'STRONG_SELL' || composite.action === 'SELL') {
          if (basicTrend.trend === 'bullish') {
            // 上昇トレンドだが指標は売りシグナル → 天井警戒
            finalTrend = 'reversal_down';
            shouldTrade = false;
            tradeAction = 'SELL';
            reason = `${basicTrend.reason} だが ${composite.description}`;
          } else {
            finalTrend = 'bearish';
            shouldTrade = false;
            tradeAction = 'WAIT';
            reason = composite.description;
          }
        }
      } else if (composite.confidence === 'medium') {
        // 中程度の確信度 → 既存トレンドを尊重しつつ参考
        if (basicTrend.trend === 'bearish' && composite.score > 20) {
          // 下落中だが買いシグナル → 慎重に買い
          shouldTrade = true;
          tradeAction = 'CAUTIOUS_BUY';
          reason = `${basicTrend.reason}、ただし ${composite.description}`;
        } else if (basicTrend.trend === 'bullish' && composite.score < -20) {
          // 上昇中だが売りシグナル → 利確検討
          tradeAction = 'TAKE_PROFIT';
          reason = `${basicTrend.reason}、ただし ${composite.description}`;
        }
      }

      // 下落トレンド + 売られすぎ指標 → 買い許可
      const rsi = composite.indicators?.rsi;
      if (basicTrend.trend === 'bearish' && rsi?.oversold) {
        shouldTrade = true;
        tradeAction = 'BUY_DIP';
        reason = `下落中だがRSI=${rsi.value}で売られすぎ → 押し目買い`;
      }

      return {
        trend: finalTrend,
        basicTrend: basicTrend.trend,
        ma: basicTrend.ma,
        deviation: basicTrend.deviation,
        shouldTrade,
        tradeAction,
        reason,
        composite,
        indicators: composite.indicators
      };
    } catch (error) {
      console.error(`[Analysis] 拡張トレンド判断エラー: ${error.message}`);
      return {
        trend: 'neutral',
        shouldTrade: false,
        tradeAction: 'HOLD',
        reason: 'エラー',
        error: error.message
      };
    }
  }

  /**
   * 買いエントリー条件をチェック
   * グリッド戦略用：「今買っていいか」を判断
   */
  async shouldBuy(pair, currentPrice) {
    const enhanced = await this.getEnhancedTrend(pair, currentPrice);
    
    // 買い許可条件
    const allowedActions = ['BUY', 'CAUTIOUS_BUY', 'BUY_DIP', 'HOLD'];
    const allowedTrends = ['bullish', 'neutral', 'reversal_up'];
    
    const canBuy = enhanced.shouldTrade && 
                   (allowedActions.includes(enhanced.tradeAction) || 
                    allowedTrends.includes(enhanced.trend));
    
    return {
      canBuy,
      reason: enhanced.reason,
      trend: enhanced.trend,
      action: enhanced.tradeAction,
      score: enhanced.composite?.score || 0,
      rsi: enhanced.indicators?.rsi?.value,
      bb: enhanced.indicators?.bb?.percentB,
      macd: enhanced.indicators?.macd?.histogram
    };
  }

  /**
   * 売りエントリー条件をチェック
   * グリッド戦略用：「今売っていいか」を判断
   */
  async shouldSell(pair, currentPrice) {
    const enhanced = await this.getEnhancedTrend(pair, currentPrice);
    
    // 売り許可条件（利確や天井警戒）
    const sellSignals = ['SELL', 'STRONG_SELL', 'TAKE_PROFIT'];
    const sellTrends = ['bearish', 'reversal_down'];
    
    const shouldSell = sellSignals.includes(enhanced.tradeAction) ||
                       sellTrends.includes(enhanced.trend);
    
    return {
      shouldSell,
      urgent: enhanced.composite?.confidence === 'high' && enhanced.tradeAction === 'STRONG_SELL',
      reason: enhanced.reason,
      trend: enhanced.trend,
      action: enhanced.tradeAction,
      score: enhanced.composite?.score || 0
    };
  }
}

module.exports = new Analysis();
