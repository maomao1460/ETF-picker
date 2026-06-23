// 作者：相空
import { useState } from 'react';
import { api } from '../api/client';
import { useApi } from '../api/hooks';
import { getBreadthText, getMa200Meta, getSentimentMeta } from '../utils/signal';

export default function History() {
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const history = useApi(() => api.getSignalHistory(50), []);
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const detail = useApi<any>(
    () => selectedId ? api.getSignal(selectedId) : Promise.resolve(null),
    [selectedId]
  );
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const signals = (history.data || []) as any[];
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const sig = detail.data as any;
  const market = (sig?.market ?? null) as Record<string, unknown> | null;
  const sentimentMeta = getSentimentMeta(market);
  const breadthText = getBreadthText(market);
  const ma200Meta = getMa200Meta(market, sig?.params?.ma200_risk as boolean | undefined);

  return (
    <div>
      <div className="page-header">
        <div>
          <h2>🕐 历史信号</h2>
          <p className="subtitle">查看所有历史信号记录与详情</p>
        </div>
      </div>
      <div style={{ display: 'flex', gap: 20 }}>
        <div style={{ width: 340, flexShrink: 0 }}>
          <div className="card">
            <div className="card-header">
              <span className="card-title">信号列表</span>
              <span className="card-subtitle">{signals.length} 条</span>
            </div>
            {history.loading && <div className="loading"><span className="spinner" /> 加载中...</div>}
            {signals.length === 0 && !history.loading && (
              <div className="empty-state"><div className="icon">📭</div><p>暂无历史信号</p></div>
            )}
            {signals.map((s: Record<string, unknown>) => (
              <div key={s.id as number} className="signal-item" style={{
                cursor: 'pointer',
                background: selectedId === s.id ? 'var(--accent-glow)' : undefined,
                borderLeft: selectedId === s.id ? '3px solid var(--accent)' : '3px solid transparent',
              }} onClick={() => setSelectedId(s.id as number)}>
                <div style={{ fontWeight: 600 }}>{s.date as string}</div>
                <div className="muted" style={{ fontSize: '0.78rem' }}>{s.params_summary as string || '默认'}</div>
              </div>
            ))}
          </div>
        </div>
        <div style={{ flex: 1 }}>
          {!selectedId && <div className="card"><div className="empty-state"><div className="icon">👈</div><p>选择信号查看详情</p></div></div>}
          {selectedId && detail.loading && <div className="loading"><span className="spinner" /></div>}
          {sig && (
            <div className="card">
              <div className="card-header"><span className="card-title">📡 {sig.date}</span></div>
              {sig.market && (
                <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 16 }}>
                  {sentimentMeta && (
                    <span className={`badge ${sentimentMeta.badgeClass}`}>
                      {sentimentMeta.text}
                    </span>
                  )}
                  {breadthText && (
                    <span className="badge badge-yellow">
                      宽度 {breadthText}
                    </span>
                  )}
                  {ma200Meta && (
                    <span className={`badge ${ma200Meta.badgeClass}`}>
                      {ma200Meta.label}
                    </span>
                  )}
                </div>
              )}
              <div className="signal-item" style={{ borderBottom: '1px solid var(--border)', paddingBottom: 6, marginBottom: 4 }}>
                <span style={{ color: 'var(--text-muted)', width: 24, fontSize: '0.75rem' }}>#</span>
                <span className="sector-name" style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>赛道</span>
                <span className="text-right" style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>综合评分</span>
              </div>
              {sig.rankings?.slice(0, 10).map((r: Record<string, unknown>, i: number) => (
                <div className="signal-item" key={i}>
                  <span style={{ color: 'var(--text-muted)', width: 24 }}>{i + 1}</span>
                  <span className="sector-name">{r.sector as string}</span>
                  <span className="text-right">{Number(r.score).toFixed(2)}</span>
                </div>
              ))}
              {sig.actions && ['sell', 'sold', 'locked', 'hold', 'buy', 'bought'].map(a => {
                const items = sig.actions[a];
                if (!items?.length) return null;
                const labels: Record<string, string> = { sell: '📉卖', sold: '📉卖', locked: '🔒锁', hold: '✅持', buy: '📈买', bought: '📈买' };
                return (
                  <div key={a} className="signal-section" style={{ marginTop: 12 }}>
                    <div className="signal-section-title">{labels[a]} ({items.length})</div>
                    {items.map((s: Record<string, unknown>, i: number) => (
                      <div className="signal-item" key={i}><span className="sector-name">{s.sector as string}</span></div>
                    ))}
                  </div>
                );
              })}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
