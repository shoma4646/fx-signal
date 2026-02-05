const bitflyer = require('../lib/bitflyer');
const notify = require('../lib/notify');
const analysis = require('../lib/analysis');
const safety = require('../lib/safety');
const fs = require('fs');
const path = require('path');

const STATE_FILE = path.join(__dirname, '../data/grid-state.json');
const REBALANCE_THRESHOLD = 3.0; // 3%ずれたらリバランス（頻繁なリセット防止）
const STOP_LOSS_THRESHOLD = -5; // -5%で損切り（安全重視）

class GridStrategy {
  constructor(name, pair, settings, dryRun = true) {
    this.name = name;      // 戦略名 (例: ETH_WIDE)
    this.pair = pair;      // 通貨ペア (例: ETH_JPY)
    this.settings = settings;
    this.dryRun = dryRun;
    this.direction = settings.direction || 'long';  // 'long' or 'short'
    this.trendFollow = settings.trendFollow || false;  // トレンドフォロー有効化
    this.state = this.loadState();
  }

  loadState() {
    if (fs.existsSync(STATE_FILE)) {
      const allState = JSON.parse(fs.readFileSync(STATE_FILE, 'utf8'));
      return allState[this.name] || this.defaultState();
    }
    return this.defaultState();
  }

  defaultState() {
    return {
      basePrice: null,           // グリッドの基準価格（固定）
      gridLevels: [],            // グリッドレベル情報
      position: 0,               // 現在のポジション量（ショートの場合は負の値）
      avgEntryPrice: 0,          // 平均エントリー価格
      avgBuyPrice: 0,            // 後方互換のため残す
      totalProfit: 0,            // 累計利益
      tradeCount: 0,             // 取引回数
      lastUpdate: null,
      configHash: null           // 設定のハッシュ（変更検知用）
    };
  }

  // 設定のハッシュを生成
  getConfigHash() {
    const key = `${this.settings.gridSpacingPercent}-${this.settings.gridCount}-${this.direction}`;
    return key;
  }

  saveState() {
    let allState = {};
    if (fs.existsSync(STATE_FILE)) {
      allState = JSON.parse(fs.readFileSync(STATE_FILE, 'utf8'));
    }
    allState[this.name] = {
      ...this.state,
      pair: this.pair,
      lastUpdate: new Date().toISOString()
    };
    fs.writeFileSync(STATE_FILE, JSON.stringify(allState, null, 2));
  }

  // グリッドを初期化（移動平均で基準価格を設定）
  async initializeGrid(currentPrice) {
    const { gridCount, gridSpacingPercent } = this.settings;
    
    // 移動平均を取得して基準価格に
    const ma = await analysis.getMovingAverage(this.pair, 24);
    this.state.basePrice = ma || currentPrice;
    this.state.gridLevels = [];
    
    const priceSource = ma ? '24h移動平均' : '現在価格';
    const directionLabel = this.direction === 'short' ? '📉ショート' : '📈ロング';

    if (this.direction === 'long') {
      // ロング戦略: 買いグリッド（下方向）、売りグリッド（上方向）
      for (let i = 1; i <= gridCount; i++) {
        const price = Math.floor(currentPrice * (1 - gridSpacingPercent * i / 100));
        this.state.gridLevels.push({
          type: 'BUY',
          level: i,
          price: price,
          triggered: false
        });
      }
      for (let i = 1; i <= gridCount; i++) {
        const price = Math.floor(currentPrice * (1 + gridSpacingPercent * i / 100));
        this.state.gridLevels.push({
          type: 'SELL',
          level: i,
          price: price,
          triggered: false
        });
      }
    } else {
      // ショート戦略: 売りグリッド（上方向）、買い戻しグリッド（下方向）
      for (let i = 1; i <= gridCount; i++) {
        const price = Math.floor(currentPrice * (1 + gridSpacingPercent * i / 100));
        this.state.gridLevels.push({
          type: 'SHORT',  // ショートエントリー
          level: i,
          price: price,
          triggered: false
        });
      }
      for (let i = 1; i <= gridCount; i++) {
        const price = Math.floor(currentPrice * (1 - gridSpacingPercent * i / 100));
        this.state.gridLevels.push({
          type: 'COVER',  // ショートカバー（買い戻し）
          level: i,
          price: price,
          triggered: false
        });
      }
    }

    console.log(`[${this.name}] 📐 グリッド初期化 ${directionLabel} 基準: ¥${this.state.basePrice.toLocaleString()} (${priceSource}, 間隔: ${gridSpacingPercent}%)`);
    this.state.gridLevels.forEach(g => {
      const emoji = (g.type === 'BUY' || g.type === 'COVER') ? '🟢' : '🔴';
      console.log(`[${this.name}]    ${emoji} ${g.type} Lv${g.level}: ¥${g.price.toLocaleString()}`);
    });

    this.saveState();
  }

  async execute() {
    try {
      const ticker = await bitflyer.getTicker(this.pair);
      const currentPrice = ticker.ltp;
      const symbol = this.pair.replace('_JPY', '');

      // 初回 or グリッド未設定なら初期化
      if (!this.state.basePrice || this.state.gridLevels.length === 0) {
        await this.initializeGrid(currentPrice);
        this.state.configHash = this.getConfigHash();
        this.saveState();
        return { success: true, price: currentPrice, action: 'initialized' };
      }

      // config変更検知 → グリッドリセット
      const currentConfigHash = this.getConfigHash();
      if (this.state.configHash && this.state.configHash !== currentConfigHash) {
        console.log(`[${this.name}] ⚙️ 設定変更検知！グリッドをリセットします`);
        console.log(`[${this.name}]    旧: ${this.state.configHash} → 新: ${currentConfigHash}`);
        await notify.sendDiscord(`⚙️ **${this.name}** 設定変更検知 → グリッドリセット`);
        this.state.basePrice = null;
        this.state.gridLevels = [];
        await this.initializeGrid(currentPrice);
        this.state.configHash = currentConfigHash;
        this.saveState();
        return { success: true, price: currentPrice, action: 'config_changed' };
      }

      // 自動リバランスチェック（基準から3%以上ずれたら）
      const deviation = analysis.calculateDeviation(currentPrice, this.state.basePrice);
      
      // トレンドフォローチェック（FX戦略用）
      if (this.trendFollow && this.state.position === 0) {
        const trendInfo = await analysis.getTrend(this.pair, currentPrice);
        
        // トレンドに逆らう場合はスキップ
        if (this.direction === 'long' && trendInfo.trend === 'bearish') {
          console.log(`[${this.name}] ⏸️ 下落トレンド中 → ロングスキップ (${trendInfo.reason})`);
          return { success: true, price: currentPrice, action: 'trend_skip' };
        }
        if (this.direction === 'short' && trendInfo.trend === 'bullish') {
          console.log(`[${this.name}] ⏸️ 上昇トレンド中 → ショートスキップ (${trendInfo.reason})`);
          return { success: true, price: currentPrice, action: 'trend_skip' };
        }
        
        // レンジ相場では両方稼働
        if (trendInfo.trend !== 'neutral') {
          console.log(`[${this.name}] 📊 トレンド: ${trendInfo.reason}`);
        }
      }
      
      // 損切りチェック（ポジションありで-5%以下）
      if (this.state.position > 0 && deviation <= STOP_LOSS_THRESHOLD) {
        console.log(`[${this.name}] 🛑 損切り発動！（乖離: ${deviation.toFixed(1)}%）`);
        await this.executeStopLoss(currentPrice);
        return { success: true, price: currentPrice, action: 'stop_loss' };
      }
      
      if (Math.abs(deviation) >= REBALANCE_THRESHOLD && this.state.position === 0) {
        console.log(`[${this.name}] 🔄 自動リバランス（乖離: ${deviation.toFixed(1)}%）`);
        await notify.sendDiscord(`🔄 **${this.name}** 自動リバランス（乖離: ${deviation.toFixed(1)}%）`);
        await this.initializeGrid(currentPrice);
        return { success: true, price: currentPrice, action: 'rebalanced' };
      }

      const posDisplay = this.direction === 'short' ? this.state.position : this.state.position;
      console.log(`[${this.name}] 現在: ¥${currentPrice.toLocaleString()} | 基準: ¥${this.state.basePrice.toLocaleString()} | ポジ: ${posDisplay} | 乖離: ${deviation.toFixed(1)}%`);

      if (this.direction === 'long') {
        // ===== ロング戦略 =====
        // 買いグリッドチェック
        const buyLevels = this.state.gridLevels
          .filter(g => g.type === 'BUY' && !g.triggered)
          .sort((a, b) => b.price - a.price);

        for (const level of buyLevels) {
          if (currentPrice <= level.price) {
            if (this.state.position < this.settings.maxPosition) {
              await this.executeBuy(level, currentPrice);
            }
          }
        }

        // 売りグリッドチェック（ポジションがある時のみ）
        if (this.state.position > 0) {
          const sellLevels = this.state.gridLevels
            .filter(g => g.type === 'SELL' && !g.triggered)
            .sort((a, b) => a.price - b.price);

          for (const level of sellLevels) {
            if (currentPrice >= level.price) {
              await this.executeSell(level, currentPrice);
              break;
            }
          }

          // 利確チェック
          const avgPrice = this.state.avgEntryPrice || this.state.avgBuyPrice;
          const takeProfitPrice = avgPrice * (1 + this.settings.takeProfitPercent / 100);
          if (currentPrice >= takeProfitPrice && this.state.position > 0) {
            await this.executeTakeProfit(currentPrice);
          }
        }
      } else {
        // ===== ショート戦略 =====
        // ショートエントリーチェック（価格が上がったら売る）
        const shortLevels = this.state.gridLevels
          .filter(g => g.type === 'SHORT' && !g.triggered)
          .sort((a, b) => a.price - b.price);

        for (const level of shortLevels) {
          if (currentPrice >= level.price) {
            if (Math.abs(this.state.position) < this.settings.maxPosition) {
              await this.executeShort(level, currentPrice);
            }
          }
        }

        // カバー（買い戻し）チェック（ショートポジションがある時のみ）
        if (this.state.position < 0) {
          const coverLevels = this.state.gridLevels
            .filter(g => g.type === 'COVER' && !g.triggered)
            .sort((a, b) => b.price - a.price);

          for (const level of coverLevels) {
            if (currentPrice <= level.price) {
              await this.executeCover(level, currentPrice);
              break;
            }
          }

          // 利確チェック（ショートの場合は価格が下がったら利確）
          const avgPrice = this.state.avgEntryPrice;
          const takeProfitPrice = avgPrice * (1 - this.settings.takeProfitPercent / 100);
          if (currentPrice <= takeProfitPrice && this.state.position < 0) {
            await this.executeTakeProfitShort(currentPrice);
          }
        }
      }

      this.saveState();
      return { success: true, price: currentPrice };

    } catch (error) {
      console.error(`[${this.pair}] エラー:`, error.message);
      await notify.notifyError(`${this.pair}: ${error.message}`);
      return { success: false, error: error.message };
    }
  }

  async executeBuy(level, currentPrice) {
    const size = this.settings.orderSize;
    const price = currentPrice; // 成行相当
    const requiredJpy = price * size * 1.01; // 手数料込みで1%余裕

    console.log(`[${this.name}] 🟢 買いシグナル Lv${level.level} @ ¥${price.toLocaleString()}`);

    // 残高チェック（本番のみ）
    if (!this.dryRun) {
      try {
        const balances = await bitflyer.getBalance();
        const jpyBalance = balances.find(b => b.currency_code === 'JPY')?.available || 0;
        
        if (jpyBalance < requiredJpy) {
          console.log(`[${this.name}] ⚠️ JPY残高不足 (必要: ¥${requiredJpy.toFixed(0)}, 利用可能: ¥${jpyBalance.toFixed(0)})`);
          await notify.notifyInsufficientBalance(this.name, this.pair, 'BUY', `¥${requiredJpy.toFixed(0)}`, `¥${jpyBalance.toFixed(0)}`);
          return;
        }
      } catch (error) {
        console.error(`[${this.name}] ❌ 残高取得失敗:`, error.message);
        await notify.notifyError(`${this.name}: 残高取得失敗 - ${error.message}`);
        return;
      }
    }

    if (this.dryRun) {
      console.log(`[${this.name}] (DRY RUN)`);
    } else {
      try {
        await bitflyer.sendOrder({
          product_code: this.pair,
          child_order_type: 'MARKET',
          side: 'BUY',
          size: size
        });
      } catch (error) {
        console.error(`[${this.name}] ❌ 買い注文失敗 - 状態更新スキップ`);
        return; // 注文失敗時は状態を更新しない
      }
    }

    // 状態更新（注文成功時のみ）
    const newPosition = this.state.position + size;
    this.state.avgBuyPrice = 
      (this.state.avgBuyPrice * this.state.position + price * size) / newPosition;
    this.state.position = newPosition;
    this.state.tradeCount++;
    level.triggered = true;

    await notify.notifyTrade(this.pair, 'BUY', price, size);
    
    // ステラに妥当性チェック依頼（本番のみ）
    if (!this.dryRun) {
      try {
        const balances = await bitflyer.getBalance();
        const jpyBalance = balances.find(b => b.currency_code === 'JPY')?.available || 0;
        const symbol = this.pair.replace('_JPY', '');
        const cryptoBalance = balances.find(b => b.currency_code === symbol)?.available || 0;
        
        await notify.requestValidation(this.name, this.pair, 'BUY', price, size, {
          position: this.state.position,
          avgPrice: this.state.avgBuyPrice,
          jpyBalance,
          cryptoBalance
        });
      } catch (error) {
        console.error(`[${this.name}] 妥当性チェック依頼失敗:`, error.message);
      }
    }
  }

  async executeSell(level, currentPrice) {
    const size = Math.min(this.settings.orderSize, this.state.position);
    const profit = (currentPrice - this.state.avgBuyPrice) * size;
    const symbol = this.pair.replace('_JPY', '');

    console.log(`[${this.name}] 🔴 売りシグナル Lv${level.level} @ ¥${currentPrice.toLocaleString()} (損益: ¥${profit.toFixed(0)})`);

    // 残高チェック（本番のみ）- 実際に持っているか確認
    if (!this.dryRun) {
      try {
        const balances = await bitflyer.getBalance();
        const cryptoBalance = balances.find(b => b.currency_code === symbol)?.available || 0;
        
        if (cryptoBalance < size) {
          console.log(`[${this.name}] ⚠️ ${symbol}残高不足 (必要: ${size}, 利用可能: ${cryptoBalance})`);
          await notify.notifyInsufficientBalance(this.name, this.pair, 'SELL', size, cryptoBalance);
          return;
        }
      } catch (error) {
        console.error(`[${this.name}] ❌ 残高取得失敗:`, error.message);
        await notify.notifyError(`${this.name}: 残高取得失敗 - ${error.message}`);
        return;
      }
    }

    if (this.dryRun) {
      console.log(`[${this.name}] (DRY RUN)`);
    } else {
      try {
        await bitflyer.sendOrder({
          product_code: this.pair,
          child_order_type: 'MARKET',
          side: 'SELL',
          size: size
        });
      } catch (error) {
        console.error(`[${this.name}] ❌ 売り注文失敗 - 状態更新スキップ`);
        return;
      }
    }

    // 状態更新（注文成功時のみ）
    this.state.position -= size;
    if (this.state.position <= 0) {
      this.state.position = 0;
      this.state.avgBuyPrice = 0;
    }
    this.state.totalProfit += profit;
    this.state.tradeCount++;
    level.triggered = true;

    await notify.notifyTrade(this.pair, 'SELL', currentPrice, size, profit);
    await safety.recordTrade(profit, 5000);
  }

  async executeTakeProfit(currentPrice) {
    const size = this.state.position;
    const profit = (currentPrice - this.state.avgBuyPrice) * size;

    console.log(`[${this.name}] 💰 利確！ @ ¥${currentPrice.toLocaleString()} (損益: ¥${profit.toFixed(0)})`);

    if (this.dryRun) {
      console.log(`[${this.name}] (DRY RUN)`);
    } else {
      await bitflyer.sendOrder({
        product_code: this.pair,
        child_order_type: 'MARKET',
        side: 'SELL',
        size: size
      });
    }

    // 状態更新 & グリッドリセット
    this.state.totalProfit += profit;
    this.state.tradeCount++;
    this.state.position = 0;
    this.state.avgBuyPrice = 0;
    this.state.basePrice = null; // 次回グリッド再初期化
    this.state.gridLevels = [];

    await notify.notifyTrade(this.pair, 'SELL', currentPrice, size, profit);
    await notify.sendDiscord(`💰 **${this.name} 利確完了！** グリッドをリセットします`);
    await safety.recordTrade(profit, 5000);
  }

  // ショートエントリー実行
  async executeShort(level, currentPrice) {
    const size = this.settings.orderSize;
    const symbol = this.pair.replace('_JPY', '');

    console.log(`[${this.name}] 🔴 ショートエントリー Lv${level.level} @ ¥${currentPrice.toLocaleString()}`);

    // 残高チェック（本番のみ）- 売る分のコインを持っているか確認
    if (!this.dryRun) {
      try {
        const balances = await bitflyer.getBalance();
        const cryptoBalance = balances.find(b => b.currency_code === symbol)?.available || 0;
        
        if (cryptoBalance < size) {
          console.log(`[${this.name}] ⚠️ ${symbol}残高不足 (必要: ${size}, 利用可能: ${cryptoBalance})`);
          await notify.notifyInsufficientBalance(this.name, this.pair, 'SHORT', size, cryptoBalance);
          return;
        }
      } catch (error) {
        console.error(`[${this.name}] ❌ 残高取得失敗:`, error.message);
        await notify.notifyError(`${this.name}: 残高取得失敗 - ${error.message}`);
        return;
      }
    }

    if (this.dryRun) {
      console.log(`[${this.name}] (DRY RUN)`);
    } else {
      try {
        await bitflyer.sendOrder({
          product_code: this.pair,
          child_order_type: 'MARKET',
          side: 'SELL',
          size: size
        });
      } catch (error) {
        console.error(`[${this.name}] ❌ ショートエントリー失敗 - 状態更新スキップ`);
        return;
      }
    }

    // 状態更新（ショートなのでpositionは負の値）
    const newPosition = this.state.position - size;
    if (this.state.position === 0) {
      this.state.avgEntryPrice = currentPrice;
    } else {
      this.state.avgEntryPrice = 
        (this.state.avgEntryPrice * Math.abs(this.state.position) + currentPrice * size) / Math.abs(newPosition);
    }
    this.state.position = newPosition;
    this.state.tradeCount++;
    level.triggered = true;

    await notify.notifyTrade(this.pair, 'SHORT', currentPrice, size);
    
    // ステラに妥当性チェック依頼（本番のみ）
    if (!this.dryRun) {
      try {
        const balances = await bitflyer.getBalance();
        const jpyBalance = balances.find(b => b.currency_code === 'JPY')?.available || 0;
        const cryptoBalance = balances.find(b => b.currency_code === symbol)?.available || 0;
        
        await notify.requestValidation(this.name, this.pair, 'SHORT', currentPrice, size, {
          position: this.state.position,
          avgPrice: this.state.avgEntryPrice,
          jpyBalance,
          cryptoBalance
        });
      } catch (error) {
        console.error(`[${this.name}] 妥当性チェック依頼失敗:`, error.message);
      }
    }
  }

  // ショートカバー（買い戻し）実行
  async executeCover(level, currentPrice) {
    const size = Math.min(this.settings.orderSize, Math.abs(this.state.position));
    const profit = (this.state.avgEntryPrice - currentPrice) * size;  // ショートは売値-買値
    const requiredJpy = currentPrice * size * 1.01; // 手数料込みで1%余裕

    console.log(`[${this.name}] 🟢 ショートカバー Lv${level.level} @ ¥${currentPrice.toLocaleString()} (損益: ¥${profit.toFixed(0)})`);

    // 残高チェック（本番のみ）- 買い戻す分のJPYがあるか確認
    if (!this.dryRun) {
      try {
        const balances = await bitflyer.getBalance();
        const jpyBalance = balances.find(b => b.currency_code === 'JPY')?.available || 0;
        
        if (jpyBalance < requiredJpy) {
          console.log(`[${this.name}] ⚠️ JPY残高不足 (必要: ¥${requiredJpy.toFixed(0)}, 利用可能: ¥${jpyBalance.toFixed(0)})`);
          await notify.notifyInsufficientBalance(this.name, this.pair, 'COVER', `¥${requiredJpy.toFixed(0)}`, `¥${jpyBalance.toFixed(0)}`);
          return;
        }
      } catch (error) {
        console.error(`[${this.name}] ❌ 残高取得失敗:`, error.message);
        await notify.notifyError(`${this.name}: 残高取得失敗 - ${error.message}`);
        return;
      }
    }

    if (this.dryRun) {
      console.log(`[${this.name}] (DRY RUN)`);
    } else {
      try {
        await bitflyer.sendOrder({
          product_code: this.pair,
          child_order_type: 'MARKET',
          side: 'BUY',
          size: size
        });
      } catch (error) {
        console.error(`[${this.name}] ❌ ショートカバー失敗 - 状態更新スキップ`);
        return;
      }
    }

    // 状態更新
    this.state.position += size;
    if (this.state.position >= 0) {
      this.state.position = 0;
      this.state.avgEntryPrice = 0;
    }
    this.state.totalProfit += profit;
    this.state.tradeCount++;
    level.triggered = true;

    await notify.notifyTrade(this.pair, 'COVER', currentPrice, size, profit);
    await safety.recordTrade(profit, 5000);
  }

  // ショート利確実行
  async executeTakeProfitShort(currentPrice) {
    const size = Math.abs(this.state.position);
    const profit = (this.state.avgEntryPrice - currentPrice) * size;

    console.log(`[${this.name}] 💰 ショート利確！ @ ¥${currentPrice.toLocaleString()} (損益: ¥${profit.toFixed(0)})`);

    if (this.dryRun) {
      console.log(`[${this.name}] (DRY RUN)`);
    } else {
      await bitflyer.sendOrder({
        product_code: this.pair,
        child_order_type: 'MARKET',
        side: 'BUY',
        size: size
      });
    }

    // 状態更新 & グリッドリセット
    this.state.totalProfit += profit;
    this.state.tradeCount++;
    this.state.position = 0;
    this.state.avgEntryPrice = 0;
    this.state.basePrice = null;
    this.state.gridLevels = [];

    await notify.notifyTrade(this.pair, 'COVER', currentPrice, size, profit);
    await notify.sendDiscord(`💰 **${this.name} ショート利確完了！** グリッドをリセットします`);
    await safety.recordTrade(profit, 5000);
  }

  // 損切り実行
  async executeStopLoss(currentPrice) {
    const size = this.state.position;
    const loss = (currentPrice - this.state.avgBuyPrice) * size;

    console.log(`[${this.name}] 🛑 損切り @ ¥${currentPrice.toLocaleString()} (損失: ¥${loss.toFixed(0)})`);

    if (this.dryRun) {
      console.log(`[${this.name}] (DRY RUN)`);
    } else {
      await bitflyer.sendOrder({
        product_code: this.pair,
        child_order_type: 'MARKET',
        side: 'SELL',
        size: size
      });
    }

    // 状態更新 & グリッドリセット
    this.state.totalProfit += loss;
    this.state.tradeCount++;
    this.state.position = 0;
    this.state.avgBuyPrice = 0;
    this.state.basePrice = null;
    this.state.gridLevels = [];

    await notify.notifyTrade(this.pair, 'SELL', currentPrice, size, loss);
    await notify.sendDiscord(`🛑 **${this.name} 損切り！** ¥${loss.toFixed(0)} | 下落トレンドのためグリッドリセット`);
    await safety.recordTrade(loss, 5000);
    
    this.saveState();
  }

  // グリッドを手動リセット
  resetGrid() {
    this.state.basePrice = null;
    this.state.gridLevels = [];
    this.saveState();
  }

  getStats() {
    return {
      name: this.name,
      pair: this.pair,
      basePrice: this.state.basePrice,
      position: this.state.position,
      avgBuyPrice: this.state.avgBuyPrice,
      totalProfit: this.state.totalProfit,
      tradeCount: this.state.tradeCount,
      gridLevels: this.state.gridLevels,
      lastUpdate: this.state.lastUpdate
    };
  }
}

module.exports = GridStrategy;
