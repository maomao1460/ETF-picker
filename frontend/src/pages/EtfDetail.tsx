// 作者：相空
import { useState, useMemo } from 'react';
import { useParams } from 'react-router-dom';
import { LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, ReferenceDot } from 'recharts';
import { api } from '../api/client';
import { useApi } from '../api/hooks';
import { normalizeSignalAction } from '../utils/signal';

export default function EtfDetail() {
  const { code: routeCode } = useParams<{ code: string }>();
  const etfList = useApi(() => api.getEtfList(), []);
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const _etfResp = etfList.data as any;
  const allEtfs = (_etfResp?.items ?? (Array.isArray(_etfResp) ? _etfResp : [])) as any[];

  const [selectedCode, setSelectedCode] = useState(routeCode || '');
  const activeCode = selectedCode || (allEtfs.length > 0 ? allEtfs[0].code : '');

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const chartData = useApi<any>(
    () => activeCode ? api.getEtfChart(activeCode) : Promise.resolve(null),
    [activeCode]
  );

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const detail = chartData.data as any;

  const priceData = useMemo(() => {
    if (!detail?.prices) return [];
    return detail.prices.map((p: Record<string, unknown>, i: number) => ({
      date: (p.date as string).slice(5),
      close: p.close,
      ma5: detail.indicators?.ma5?.[i],
      ma20: detail.indicators?.ma20?.[i],
    }));
  }, [detail]);

  const signalMarkers = useMemo(() => {
    if (!detail?.signals_history || !detail?.prices) return [];
    const dateMap = new Map(detail.prices.map((p: Record<string, unknown>, i: number) => [p.date, i]));
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    return detail.signals_history
      .filter((s: any) => dateMap.has(s.date))
      .map((s: any) => {
        const action = normalizeSignalAction(s.action);
        if (!action) return null;

        const idx = dateMap.get(s.date)!;
        return {
          date: (s.date as string).slice(5),
          close: detail.prices[idx as number].close,
          action,
        };
      })
      .filter(Boolean);
  }, [detail]);

  const pct = (n: number) =>
    n != null ? `${n >= 0 ? '+' : ''}${(n * 100).toFixed(2)}%` : '—';

  return (
    <div>
      <div className="page-header">
        <div>
          <h2>📈 ETF 详情分析</h2>
          <p className="subtitle">走势图 · 技术指标 · 历史信号</p>
        </div>
      </div>

      {/* Sector Selector */}
      <div className="params-panel">
        <div className="form-row">
          <div className="form-group" style={{ minWidth: 240 }}>
            <label className="form-label">选择赛道</label>
            <select className="form-select" value={activeCode}
              onChange={e => setSelectedCode(e.target.value)}>
              {allEtfs.map((etf: Record<string, unknown>) => (
                <option key={etf.code as string} value={etf.code as string}>
                  {etf.sector as string} ({etf.code as string})
                </option>
              ))}
            </select>
          </div>
          {detail?.current_holding && (
            <span className="badge badge-green" style={{ marginBottom: 4 }}>
              🎯 当前持有 {detail.current_holding.hold_days}天
            </span>
          )}
        </div>
      </div>

      {chartData.loading && <div className="loading"><span className="spinner" /> 加载图表...</div>}

      {detail && (
        <>
          {/* Price Chart */}
          <div className="card" style={{ marginBottom: 20 }}>
            <div className="card-header">
              <span className="card-title">
                {detail.sector} ({detail.code})
              </span>
              <span className="card-subtitle">{priceData.length}日走势</span>
            </div>
            <div style={{ height: 360 }}>
              <ResponsiveContainer>
                <LineChart data={priceData} margin={{ top: 10, right: 10, bottom: 0, left: 10 }}>
                  <XAxis dataKey="date" tick={{ fontSize: 11 }} interval={Math.floor(priceData.length / 8)} />
                  <YAxis domain={['auto', 'auto']} tick={{ fontSize: 11 }} />
                  <Tooltip
                    contentStyle={{ background: '#1c1e2e', border: '1px solid #2a2d3e', borderRadius: 8, fontSize: 12 }}
                    labelStyle={{ color: '#9aa0b0' }}
                  />
                  <Line type="monotone" dataKey="close" stroke="#6c5ce7" strokeWidth={2} dot={false} name="收盘价" />
                  <Line type="monotone" dataKey="ma5" stroke="#4dabf7" strokeWidth={1} dot={false} name="MA5" strokeDasharray="4 2" />
                  <Line type="monotone" dataKey="ma20" stroke="#ff922b" strokeWidth={1} dot={false} name="MA20" strokeDasharray="4 2" />
                  {signalMarkers.map((m: Record<string, unknown>, i: number) => (
                    <ReferenceDot
                      key={i}
                      x={m.date as string}
                      y={m.close as number}
                      r={5}
                      fill={m.action === 'buy' ? '#00d68f' : '#ff6b6b'}
                      stroke="none"
                    />
                  ))}
                </LineChart>
              </ResponsiveContainer>
            </div>
          </div>

          {/* Key Metrics */}
          <div className="stat-grid">
            <div className="stat-card blue">
              <div className="stat-label">20日动量</div>
              <div className="stat-value">{pct(detail.indicators?.momentum_20d_latest)}</div>
            </div>
            <div className="stat-card accent">
              <div className="stat-label">20日波动率</div>
              <div className="stat-value">{pct(detail.indicators?.volatility_20d_latest)}</div>
            </div>
            <div className="stat-card green">
              <div className="stat-label">距250日低点</div>
              <div className="stat-value">{pct(detail.indicators?.distance_from_low)}</div>
            </div>
            <div className="stat-card red">
              <div className="stat-label">距250日高点</div>
              <div className="stat-value">{pct(detail.indicators?.distance_from_high)}</div>
            </div>
          </div>

          {/* Signal History */}
          {detail.signals_history?.length > 0 && (
            <div className="card">
              <div className="card-header">
                <span className="card-title">📡 该 ETF 历史信号</span>
                <span className="card-subtitle">{detail.signals_history.length} 条</span>
              </div>
              <div className="table-container">
                <table>
                  <thead>
                    <tr>
                      <th>日期</th>
                      <th>操作</th>
                      <th className="text-right">排名</th>
                      <th className="text-right">评分</th>
                    </tr>
                  </thead>
                  <tbody>
                    {detail.signals_history.map((s: Record<string, unknown>, i: number) => (
                      <tr key={i}>
                        <td>{s.date as string}</td>
                        <td>
                          <span className={`badge ${normalizeSignalAction(s.action) === 'buy' ? 'badge-green' : 'badge-red'}`}>
                            {normalizeSignalAction(s.action) === 'buy' ? '📈 买入' : '📉 卖出'}
                          </span>
                        </td>
                        <td className="text-right">{s.rank != null ? `#${s.rank as number}` : '—'}</td>
                        <td className="text-right">
                          {Number.isFinite(Number(s.score)) ? Number(s.score).toFixed(2) : '—'}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
}
