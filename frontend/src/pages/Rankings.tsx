// 作者：相空
import { useState } from 'react';
import { api } from '../api/client';
import { useApi } from '../api/hooks';
import { exportCSV, today } from '../utils/export';

type ViewMode = 'table' | 'heatmap';
type HeatMetric = 'score' | 'mom20' | 'mom5' | 'change_pct' | 'dist_ma5' | 'dist_ma20';

interface Breadth {
  n_up: number;
  n_down: number;
  n_flat: number;
  total: number;
  down_ratio: number;
  up_ratio: number;
  signal: 'buy' | 'caution' | 'neutral';
}

/** Map a value in [-limit, +limit] → hsl color (red → yellow → green) */
function momentumColor(v: number | null, opacity = 0.85): string {
  if (v == null) return 'rgba(255,255,255,0.05)';
  const clamped = Math.max(-0.15, Math.min(0.15, v));   // clamp ±15%
  const norm = (clamped + 0.15) / 0.30;                 // 0..1
  const hue = norm * 120;                                // 0=red → 120=green
  return `hsla(${hue}, 70%, 45%, ${opacity})`;
}

export default function Rankings() {
  const { data, loading, error, refetch } = useApi(() => api.getEtfList(), []);
  const [view, setView] = useState<ViewMode>('table');
  const [metric, setMetric] = useState<HeatMetric>('score');

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const resp = data as any;
  const breadth: Breadth | null = resp?.breadth ?? null;
  const etfList: Record<string, unknown>[] = resp?.items ?? (Array.isArray(resp) ? resp : []);

  const pct = (n: number) =>
    n != null ? `${n >= 0 ? '+' : ''}${(n * 100).toFixed(2)}%` : '—';

  const signalConfig = {
    buy:     { emoji: '🟢', label: '超卖反弹信号', desc: '下跌占比≥70%，历史后5日上涨概率65.7%', color: '#22c55e' },
    caution: { emoji: '🟡', label: '短期过热警惕', desc: '上涨占比≥65%，后续表现低于基准',         color: '#eab308' },
    neutral: { emoji: '⚪', label: '中性',          desc: '市场状态正常',                          color: '#94a3b8' },
  };

  return (
    <div>
      <div className="page-header">
        <div>
          <h2>📋 赛道排名</h2>
          <p className="subtitle">30 个赛道 ETF 实时排名 · 按综合评分排序（与信号逻辑一致）</p>
        </div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          {/* View toggle */}
          <div className="view-toggle">
            <button className={`view-btn ${view === 'table' ? 'active' : ''}`}
              onClick={() => setView('table')} title="表格">☰</button>
            <button className={`view-btn ${view === 'heatmap' ? 'active' : ''}`}
              onClick={() => setView('heatmap')} title="热力图">▦</button>
          </div>
          {view === 'heatmap' && (
            <select className="form-select" value={metric} style={{ width: 'auto', fontSize: '0.82rem' }}
              onChange={e => setMetric(e.target.value as HeatMetric)}>
              <option value="score">综合评分</option>
              <option value="mom20">20日涨幅</option>
              <option value="mom5">5日涨幅</option>
              <option value="dist_ma5">偏离MA5</option>
              <option value="dist_ma20">偏离MA20</option>
              <option value="change_pct">今日涨跌幅</option>
            </select>
          )}
          <button className="btn btn-secondary" onClick={() => {
            exportCSV(
              `ETF排名_${today()}.csv`,
              ['排名', '赛道', '代码', '最新价', '今日涨跌幅', '综合评分', '20日涨幅', '5日涨幅', '偏离MA5', '偏离MA20', '趋势'],
              etfList.map((etf, i) => [
                i + 1, etf.sector as string, etf.code as string, Number(etf.latest_price).toFixed(3),
                etf.change_pct != null ? (Number(etf.change_pct) * 100).toFixed(2) + '%' : '',
                etf.score != null ? (Number(etf.score) * 100).toFixed(2) + '%' : '',
                etf.mom20 != null ? (Number(etf.mom20) * 100).toFixed(2) + '%' : '',
                etf.mom5 != null ? (Number(etf.mom5) * 100).toFixed(2) + '%' : '',
                etf.dist_ma5 != null ? (Number(etf.dist_ma5) * 100).toFixed(2) + '%' : '',
                etf.dist_ma20 != null ? (Number(etf.dist_ma20) * 100).toFixed(2) + '%' : '',
                etf.trend === 'up' ? '上涨' : etf.trend === 'down' ? '下跌' : '震荡',
              ] as (string | number)[])
            );
          }}>📥 导出CSV</button>
          <button className="btn btn-secondary" onClick={refetch}>🔄 刷新</button>
        </div>
      </div>

      {loading && <div className="loading"><span className="spinner" /> 加载中...</div>}
      {error && <p style={{ color: 'var(--red)' }}>❌ {error}</p>}

      {/* ── 市场宽度指示条 ── */}
      {!loading && !error && breadth && (() => {
        const sc = signalConfig[breadth.signal];
        const barWidth = breadth.total > 0 ? breadth.total : 1;
        return (
          <div className="card" style={{ marginBottom: 16, padding: '16px 20px' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                <span style={{ fontSize: '1.1rem', fontWeight: 700 }}>📊 市场宽度</span>
                <span style={{
                  display: 'inline-flex', alignItems: 'center', gap: 4,
                  padding: '3px 10px', borderRadius: 12,
                  background: `${sc.color}20`, color: sc.color,
                  fontSize: '0.82rem', fontWeight: 600,
                }}>
                  {sc.emoji} {sc.label}
                </span>
              </div>
              <span style={{ fontSize: '0.78rem', color: 'var(--text-muted)' }}>{sc.desc}</span>
            </div>
            {/* 比例条 */}
            <div style={{
              display: 'flex', borderRadius: 6, overflow: 'hidden',
              height: 28, background: 'rgba(255,255,255,0.05)',
            }}>
              {breadth.n_up > 0 && (
                <div style={{
                  width: `${(breadth.n_up / barWidth) * 100}%`,
                  background: 'linear-gradient(135deg, #22c55e, #16a34a)',
                  display: 'flex', alignItems: 'center', justifyContent: 'center',
                  fontSize: '0.78rem', fontWeight: 600, color: '#fff',
                  minWidth: breadth.n_up > 0 ? 40 : 0,
                }}>
                  🟢 {breadth.n_up}
                </div>
              )}
              {breadth.n_flat > 0 && (
                <div style={{
                  width: `${(breadth.n_flat / barWidth) * 100}%`,
                  background: 'linear-gradient(135deg, #eab308, #ca8a04)',
                  display: 'flex', alignItems: 'center', justifyContent: 'center',
                  fontSize: '0.78rem', fontWeight: 600, color: '#fff',
                  minWidth: breadth.n_flat > 0 ? 40 : 0,
                }}>
                  🟡 {breadth.n_flat}
                </div>
              )}
              {breadth.n_down > 0 && (
                <div style={{
                  width: `${(breadth.n_down / barWidth) * 100}%`,
                  background: 'linear-gradient(135deg, #ef4444, #dc2626)',
                  display: 'flex', alignItems: 'center', justifyContent: 'center',
                  fontSize: '0.78rem', fontWeight: 600, color: '#fff',
                  minWidth: breadth.n_down > 0 ? 40 : 0,
                }}>
                  🔴 {breadth.n_down}
                </div>
              )}
            </div>
            {/* 文字说明 */}
            <div style={{ display: 'flex', gap: 20, marginTop: 8, fontSize: '0.78rem', color: 'var(--text-muted)' }}>
              <span>上涨 <b style={{ color: '#22c55e' }}>{breadth.n_up}</b> ({(breadth.up_ratio * 100).toFixed(0)}%)</span>
              <span>震荡 <b style={{ color: '#eab308' }}>{breadth.n_flat}</b></span>
              <span>下跌 <b style={{ color: '#ef4444' }}>{breadth.n_down}</b> ({(breadth.down_ratio * 100).toFixed(0)}%)</span>
            </div>
          </div>
        );
      })()}

      {/* Table View */}
      {!loading && !error && view === 'table' && (
        <div className="card">
          <div className="table-container">
            <table>
              <thead>
                <tr>
                  <th>#</th>
                  <th>赛道</th>
                  <th>代码</th>
                  <th className="text-right">最新价</th>
                  <th className="text-right">今日涨跌幅</th>
                  <th className="text-right">综合评分 ↓</th>
                  <th className="text-right">20日涨幅</th>
                  <th className="text-right">5日涨幅</th>
                  <th className="text-right">偏离MA5</th>
                  <th className="text-right">偏离MA20</th>
                  <th className="text-center">趋势</th>
                </tr>
              </thead>
              <tbody>
                {etfList.map((etf, i) => (
                  <tr key={etf.code as string} style={{ cursor: 'pointer' }}
                    onClick={() => window.location.href = `/etf/${etf.code}`}>
                    <td style={{ color: 'var(--text-muted)', fontWeight: 600 }}>{i + 1}</td>
                    <td style={{ fontWeight: 600 }}>{etf.sector as string}</td>
                    <td style={{ fontFamily: 'var(--font-mono)', color: 'var(--text-secondary)' }}>
                      {etf.code as string}
                    </td>
                    <td className="text-right">{Number(etf.latest_price).toFixed(3)}</td>
                    <td className={`text-right ${Number(etf.change_pct) >= 0 ? 'positive' : 'negative'}`}>
                      {pct(etf.change_pct as number)}
                    </td>
                    <td className={`text-right ${Number(etf.score) >= 0 ? 'positive' : 'negative'}`}
                      style={{ fontWeight: 600 }}>
                      {etf.score != null ? pct(etf.score as number) : '—'}
                    </td>
                    <td className={`text-right ${Number(etf.mom20) >= 0 ? 'positive' : 'negative'}`}>
                      {etf.mom20 != null ? pct(etf.mom20 as number) : '—'}
                    </td>
                    <td className={`text-right ${Number(etf.mom5) >= 0 ? 'positive' : 'negative'}`}>
                      {etf.mom5 != null ? pct(etf.mom5 as number) : '—'}
                    </td>
                    <td className={`text-right ${Number(etf.dist_ma5) >= 0 ? 'positive' : 'negative'}`}>
                      {etf.dist_ma5 != null ? pct(etf.dist_ma5 as number) : '—'}
                    </td>
                    <td className={`text-right ${Number(etf.dist_ma20) >= 0 ? 'positive' : 'negative'}`}>
                      {etf.dist_ma20 != null ? pct(etf.dist_ma20 as number) : '—'}
                    </td>
                    <td className="text-center" style={{ whiteSpace: 'nowrap' }}>
                      <span className={etf.trend === 'up' ? 'positive' : etf.trend === 'down' ? 'negative' : ''}>
                        {etf.trend === 'up' ? '🟢 上涨' : etf.trend === 'down' ? '🔴 下跌' : '🟡 震荡'}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Heatmap View */}
      {!loading && !error && view === 'heatmap' && (
        <div className="heatmap-grid">
          {etfList.map((etf) => {
            const val = etf[metric] as number | null;
            return (
              <div
                key={etf.code as string}
                className="heatmap-cell"
                style={{ background: momentumColor(val) }}
                onClick={() => window.location.href = `/etf/${etf.code}`}
              >
                <span className="heatmap-sector">{etf.sector as string}</span>
                <span className="heatmap-value">{val != null ? pct(val) : '—'}</span>
                <span className="heatmap-code">{etf.code as string}</span>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
