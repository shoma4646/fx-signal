const bitflyer = require('./bitflyer');

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
}

module.exports = new Analysis();
