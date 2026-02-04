'use client';

import { useState, useEffect } from 'react';
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, BarChart, Bar } from 'recharts';

interface Trade {
  pair: string;
  side: 'BUY' | 'SELL';
  price: number;
  size: number;
  profit: number | null;
  timestamp: string;
}

interface Stats {
  totalProfit: number;
  tradeCount: number;
  winCount: number;
  lossCount: number;
  positions: {
    name: string;
    position: number;
    avgBuyPrice: number;
    totalProfit: number;
  }[];
  lastUpdate: string;
}

export default function Dashboard() {
  const [trades, setTrades] = useState<Trade[]>([]);
  const [stats, setStats] = useState<Stats | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    // データを読み込む
    Promise.all([
      fetch('/data/trades.json').then(r => r.json()).catch(() => []),
      fetch('/data/stats.json').then(r => r.json()).catch(() => null)
    ]).then(([tradesData, statsData]) => {
      setTrades(tradesData);
      setStats(statsData);
      setLoading(false);
    });
  }, []);

  // 損益推移データを作成
  const profitData = trades
    .filter(t => t.profit !== null)
    .reduce((acc: { time: string; profit: number; cumulative: number }[], trade, index) => {
      const cumulative = (acc[index - 1]?.cumulative || 0) + (trade.profit || 0);
      acc.push({
        time: new Date(trade.timestamp).toLocaleTimeString('ja-JP', { hour: '2-digit', minute: '2-digit' }),
        profit: trade.profit || 0,
        cumulative
      });
      return acc;
    }, []);

  // ペアごとの取引回数
  const pairStats = trades.reduce((acc: Record<string, number>, trade) => {
    acc[trade.pair] = (acc[trade.pair] || 0) + 1;
    return acc;
  }, {});

  const pairData = Object.entries(pairStats).map(([pair, count]) => ({
    pair: pair.replace('_JPY', ''),
    count
  }));

  if (loading) {
    return (
      <div className="min-h-screen bg-gray-900 text-white flex items-center justify-center">
        <div className="text-xl">Loading...</div>
      </div>
    );
  }

  const totalProfit = stats?.totalProfit || profitData[profitData.length - 1]?.cumulative || 0;
  const winRate = stats ? (stats.winCount / (stats.winCount + stats.lossCount) * 100) : 0;

  return (
    <div className="min-h-screen bg-gray-900 text-white p-6">
      <div className="max-w-7xl mx-auto">
        {/* ヘッダー */}
        <div className="mb-8">
          <h1 className="text-3xl font-bold mb-2">🌟 Stella Trader Dashboard</h1>
          <p className="text-gray-400">
            Last update: {stats?.lastUpdate ? new Date(stats.lastUpdate).toLocaleString('ja-JP') : 'N/A'}
          </p>
        </div>

        {/* サマリーカード */}
        <div className="grid grid-cols-1 md:grid-cols-4 gap-4 mb-8">
          <div className="bg-gray-800 rounded-lg p-6">
            <div className="text-gray-400 text-sm mb-1">累計損益</div>
            <div className={`text-2xl font-bold ${totalProfit >= 0 ? 'text-green-400' : 'text-red-400'}`}>
              ¥{totalProfit.toLocaleString()}
            </div>
          </div>
          <div className="bg-gray-800 rounded-lg p-6">
            <div className="text-gray-400 text-sm mb-1">取引回数</div>
            <div className="text-2xl font-bold">{trades.length}回</div>
          </div>
          <div className="bg-gray-800 rounded-lg p-6">
            <div className="text-gray-400 text-sm mb-1">勝率</div>
            <div className="text-2xl font-bold">{winRate.toFixed(1)}%</div>
          </div>
          <div className="bg-gray-800 rounded-lg p-6">
            <div className="text-gray-400 text-sm mb-1">ステータス</div>
            <div className="text-2xl font-bold text-green-400">🟢 稼働中</div>
          </div>
        </div>

        {/* グラフ */}
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 mb-8">
          {/* 累計損益グラフ */}
          <div className="bg-gray-800 rounded-lg p-6">
            <h2 className="text-xl font-bold mb-4">📈 累計損益推移</h2>
            <ResponsiveContainer width="100%" height={300}>
              <LineChart data={profitData}>
                <CartesianGrid strokeDasharray="3 3" stroke="#374151" />
                <XAxis dataKey="time" stroke="#9CA3AF" fontSize={12} />
                <YAxis stroke="#9CA3AF" fontSize={12} />
                <Tooltip
                  contentStyle={{ backgroundColor: '#1F2937', border: 'none' }}
                  labelStyle={{ color: '#9CA3AF' }}
                />
                <Line
                  type="monotone"
                  dataKey="cumulative"
                  stroke="#10B981"
                  strokeWidth={2}
                  dot={false}
                />
              </LineChart>
            </ResponsiveContainer>
          </div>

          {/* ペアごとの取引回数 */}
          <div className="bg-gray-800 rounded-lg p-6">
            <h2 className="text-xl font-bold mb-4">📊 ペア別取引回数</h2>
            <ResponsiveContainer width="100%" height={300}>
              <BarChart data={pairData}>
                <CartesianGrid strokeDasharray="3 3" stroke="#374151" />
                <XAxis dataKey="pair" stroke="#9CA3AF" fontSize={12} />
                <YAxis stroke="#9CA3AF" fontSize={12} />
                <Tooltip
                  contentStyle={{ backgroundColor: '#1F2937', border: 'none' }}
                  labelStyle={{ color: '#9CA3AF' }}
                />
                <Bar dataKey="count" fill="#3B82F6" radius={[4, 4, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </div>

        {/* ポジション */}
        {stats?.positions && stats.positions.length > 0 && (
          <div className="bg-gray-800 rounded-lg p-6 mb-8">
            <h2 className="text-xl font-bold mb-4">💼 現在のポジション</h2>
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
              {stats.positions.map((pos) => (
                <div key={pos.name} className="bg-gray-700 rounded-lg p-4">
                  <div className="text-sm text-gray-400">{pos.name}</div>
                  <div className="text-lg font-bold">{pos.position}</div>
                  <div className="text-sm text-gray-400">
                    平均: ¥{pos.avgBuyPrice?.toLocaleString() || 0}
                  </div>
                  <div className={`text-sm ${pos.totalProfit >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                    損益: ¥{pos.totalProfit?.toLocaleString() || 0}
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* 取引履歴 */}
        <div className="bg-gray-800 rounded-lg p-6">
          <h2 className="text-xl font-bold mb-4">📋 直近の取引</h2>
          <div className="overflow-x-auto">
            <table className="w-full">
              <thead>
                <tr className="text-left text-gray-400 text-sm">
                  <th className="pb-3">時刻</th>
                  <th className="pb-3">ペア</th>
                  <th className="pb-3">売買</th>
                  <th className="pb-3">価格</th>
                  <th className="pb-3">数量</th>
                  <th className="pb-3">損益</th>
                </tr>
              </thead>
              <tbody>
                {trades.slice(-20).reverse().map((trade, i) => (
                  <tr key={i} className="border-t border-gray-700">
                    <td className="py-3 text-sm">
                      {new Date(trade.timestamp).toLocaleString('ja-JP')}
                    </td>
                    <td className="py-3">{trade.pair.replace('_JPY', '')}</td>
                    <td className={`py-3 ${trade.side === 'BUY' ? 'text-green-400' : 'text-red-400'}`}>
                      {trade.side === 'BUY' ? '🟢 買い' : '🔴 売り'}
                    </td>
                    <td className="py-3">¥{trade.price.toLocaleString()}</td>
                    <td className="py-3">{trade.size}</td>
                    <td className={`py-3 ${(trade.profit || 0) >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                      {trade.profit !== null ? `¥${trade.profit.toFixed(0)}` : '-'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>

        {/* フッター */}
        <div className="mt-8 text-center text-gray-500 text-sm">
          Powered by Stella 🌟
        </div>
      </div>
    </div>
  );
}
