// 作者：相空
import { useState } from 'react';
import { api } from '../api/client';
import { useApi, useSubmit } from '../api/hooks';
import { getBreadthText, getMa200Meta, getSentimentMeta } from '../utils/signal';

const DEFAULT_PARAMS = {
  top_n: 5,
  score_mode: 'rank_momentum',
  min_hold: 15,
  max_offensive: 4,
  ma200_risk: true,
  spike_filter: true,
  consec_down: 3,
  force_refresh: false,
  // 板块轮动专用
  rps_threshold: 85,
  rps60_threshold: 80,
  rps_exit_threshold: 50,
  vol_ratio_entry: 1.3,
  vol_ratio_exit: 0.8,
  vol_consec_days: 3,
  surge_min_stocks: 3,
};

export default function Dashboard() {
  const [params, setParams] = useState(DEFAULT_PARAMS);
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const [signal, setSignal] = useState<any>(null);
  const [exits, setExits] = useState<any>(null);
  const [checkingExits, setCheckingExits] = useState(false);
  const [intraday, setIntraday] = useState<any>(null);
  const [checkingIntraday, setCheckingIntraday] = useState(false);

  const portfolio = useApi(() => api.getPortfolioSummary(), []);
  const holdings = useApi(() => api.getHoldings(), [signal]);
  const { loading: generating, error: genError, submit } = useSubmit();

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const pf = portfolio.data as any;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const holdingsList = (holdings.data || []) as any[];
  const market = (signal?.market ?? null) as Record<string, unknown> | null;
  const sentimentMeta = getSentimentMeta(market);
  const breadthText = getBreadthText(market);
  const ma200Meta = getMa200Meta(market, signal?.params?.ma200_risk as boolean | undefined);

  const checkIntraday = async () => {
    setCheckingIntraday(true);
    try {
      const result = await api.intradayCheck(params.score_mode);
      setIntraday(result);
    } catch (e) {
      console.error('Intraday check failed:', e);
    } finally {
      setCheckingIntraday(false);
    }
  };

  const checkExits = async () => {
    setCheckingExits(true);
    try {
      const result = await api.checkExits(params.score_mode);
      setExits(result);
    } catch (e) {
      console.error('Exit check failed:', e);
    } finally {
      setCheckingExits(false);
    }
  };

  const handleGenerate = async () => {
    const result = await submit(() => api.generateSignal(params));
    if (result) {
      setSignal(result);
      portfolio.refetch();
      checkExits();
    }
  };

  const handleRefresh = async () => {
    await submit(() => api.refreshMarket());
    await handleGenerate();
  };

  const fmt = (n: number) =>
    n != null ? `¥${n.toLocaleString('zh-CN', { minimumFractionDigits: 0 })}` : '—';
  const pct = (n: number) =>
    n != null ? `${n >= 0 ? '+' : ''}${(n * 100).toFixed(2)}%` : '—';

  return (
    <div>
      <div className="page-header">
        <div>
          <h2>📊 操作台</h2>
          <p className="subtitle">信号生成 · 持仓概览 · 实时状态</p>
        </div>
      </div>

      {/* Portfolio Overview */}
      <div className="stat-grid">
        <div className="stat-card accent">
          <div className="stat-label">总资产</div>
          <div className="stat-value">{pf ? fmt(pf.total_value) : '—'}</div>
        </div>
        <div className="stat-card blue">
          <div className="stat-label">持仓市值</div>
          <div className="stat-value">{pf ? fmt(pf.positions_value) : '—'}</div>
        </div>
        <div className="stat-card green">
          <div className="stat-label">可用现金</div>
          <div className="stat-value">{pf ? fmt(pf.current_cash ?? pf.cash) : '—'}</div>
        </div>
        <div className="stat-card" style={{ borderTop: '3px solid var(--yellow)' }}>
          <div className="stat-label">累计盈亏</div>
          <div className="stat-value">
            {pf ? (
              <span className={pf.total_pnl >= 0 ? 'positive' : 'negative'}>
                {fmt(pf.total_pnl)} ({pct(pf.total_return)})
              </span>
            ) : '—'}
          </div>
        </div>
      </div>

      {/* Params Panel */}
      <div className="params-panel">
        <div className="card-header">
          <span className="card-title">⚙️ 信号参数</span>
        </div>
        <div className="params-grid">
          <div className="form-group">
            <label className="form-label">Top N</label>
            <select className="form-select" value={params.top_n}
              onChange={e => setParams({ ...params, top_n: +e.target.value })}>
              {[3, 4, 5, 6, 7, 8].map(n => <option key={n} value={n}>{n}</option>)}
            </select>
          </div>
          <div className="form-group">
            <label className="form-label">评分模式</label>
            <select className="form-select" value={params.score_mode}
              onChange={e => setParams({ ...params, score_mode: e.target.value })}>
              <option value="rank_momentum">排名动量</option>
              <option value="mixed">混合</option>
              <option value="pure20">纯20日</option>
              <option value="sector_rotation">板块轮动(RPS+量比)</option>
            </select>
          </div>
          <div className="form-group">
            <label className="form-label">最短持有</label>
            <input className="form-input" type="number" value={params.min_hold}
              onChange={e => setParams({ ...params, min_hold: +e.target.value })} />
          </div>
          <div className="form-group">
            <label className="form-label">进攻上限</label>
            <input className="form-input" type="number" value={params.max_offensive}
              onChange={e => setParams({ ...params, max_offensive: +e.target.value })} />
          </div>
          <div className="form-group">
            <label className="form-label">连跌天数</label>
            <input className="form-input" type="number" value={params.consec_down}
              onChange={e => setParams({ ...params, consec_down: +e.target.value })} />
          </div>
        </div>
        <div style={{ display: 'flex', gap: 16, marginTop: 12 }}>
          <label className="form-checkbox">
            <input type="checkbox" checked={params.ma200_risk}
              onChange={e => setParams({ ...params, ma200_risk: e.target.checked })} />
            MA200风控
          </label>
          <label className="form-checkbox">
            <input type="checkbox" checked={params.spike_filter}
              onChange={e => setParams({ ...params, spike_filter: e.target.checked })} />
            Spike过滤
          </label>
        </div>
        <div className="params-actions">
          <button className="btn btn-secondary" onClick={handleRefresh} disabled={generating}>
            🔄 刷新数据
          </button>
          <button className="btn btn-primary btn-lg" onClick={handleGenerate} disabled={generating}>
            {generating ? <><span className="spinner" /> 生成中...</> : '▶ 生成信号'}
          </button>
        </div>
        {genError && <p style={{ color: 'var(--red)', marginTop: 8, fontSize: '0.85rem' }}>❌ {genError}</p>}
      </div>

      {/* Signal Result */}
      {signal && (
        <div className="card" style={{ marginBottom: 20 }}>
          <div className="card-header">
            <span className="card-title">📡 信号结果 — {signal.date}</span>
            {signal.is_stale && <span className="badge badge-yellow">⚠️ 非最新</span>}
          </div>

          {/* Market Overview */}
          {signal.market && (
            <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', marginBottom: 16 }}>
              {sentimentMeta && (
                <span className={`badge ${sentimentMeta.badgeClass}`}>
                  市场: {sentimentMeta.text}
                </span>
              )}
              {breadthText && (
                <span className="badge badge-yellow">
                  宽度: {breadthText}
                </span>
              )}
              {ma200Meta && (
                <span className={`badge ${ma200Meta.badgeClass}`}>
                  {ma200Meta.label}
                </span>
              )}
              {signal.market.spike_sectors?.length > 0 && (
                <span className="badge badge-yellow">
                  ⚡ Spike: {signal.market.spike_sectors.length}只
                </span>
              )}
            </div>
          )}

          {/* Actions */}
          {signal.actions && (
            <>
              {signal.actions.sell?.length > 0 && (
                <div className="signal-section">
                  <div className="signal-section-title">
                    <span style={{ color: 'var(--red)' }}>📉 卖出</span>
                    <span className="badge badge-red">{signal.actions.sell.length}</span>
                  </div>
                  {signal.actions.sell.map((s: Record<string, unknown>, i: number) => (
                    <div className="signal-item" key={i}>
                      <span className="sector-name">{s.sector as string}</span>
                      <span className="detail">持{s.hold_days as number}天</span>
                      <span className={Number(s.pnl_pct) >= 0 ? 'positive' : 'negative'}>
                        {pct(s.pnl_pct as number)}
                      </span>
                      <span className="muted">{s.reason as string}</span>
                    </div>
                  ))}
                </div>
              )}

              {signal.actions.locked?.length > 0 && (
                <div className="signal-section">
                  <div className="signal-section-title">
                    <span style={{ color: 'var(--yellow)' }}>🔒 锁定</span>
                    <span className="badge badge-yellow">{signal.actions.locked.length}</span>
                  </div>
                  {signal.actions.locked.map((s: Record<string, unknown>, i: number) => (
                    <div className="signal-item" key={i}>
                      <span className="sector-name">{s.sector as string}</span>
                      <span className="detail">持{s.hold_days as number}天 (还需{s.remaining as number}天)</span>
                      <span className={Number(s.pnl_pct) >= 0 ? 'positive' : 'negative'}>
                        {pct(s.pnl_pct as number)}
                      </span>
                    </div>
                  ))}
                </div>
              )}

              {signal.actions.hold?.length > 0 && (
                <div className="signal-section">
                  <div className="signal-section-title">
                    <span style={{ color: 'var(--green)' }}>✅ 持有</span>
                    <span className="badge badge-green">{signal.actions.hold.length}</span>
                  </div>
                  {signal.actions.hold.map((s: Record<string, unknown>, i: number) => (
                    <div className="signal-item" key={i}>
                      <span className="sector-name">{s.sector as string}</span>
                      <span className="detail">持{s.hold_days as number}天</span>
                      <span className={Number(s.pnl_pct) >= 0 ? 'positive' : 'negative'}>
                        {pct(s.pnl_pct as number)}
                      </span>
                    </div>
                  ))}
                </div>
              )}

              {signal.actions.buy?.length > 0 && (
                <div className="signal-section">
                  <div className="signal-section-title">
                    <span style={{ color: 'var(--blue)' }}>📈 买入建议</span>
                    <span className="badge badge-blue">{signal.actions.buy.length}</span>
                  </div>
                  {signal.actions.buy.map((s: Record<string, unknown>, i: number) => (
                    <div className="signal-item" key={i}>
                      <span className="sector-name">{s.sector as string}</span>
                      <span className="detail">¥{Number(s.price).toFixed(3)}</span>
                      <span className="muted">{s.suggested_shares as number}股 ≈ ¥{Number(s.amount).toLocaleString()}</span>
                    </div>
                  ))}
                </div>
              )}
            </>
          )}
        </div>
      )}

      {/* Current Holdings */}
      <div className="card">
        <div className="card-header">
          <span className="card-title">💼 当前持仓</span>
          <span className="card-subtitle">{holdingsList.length} 只</span>
        </div>
        {holdingsList.length === 0 ? (
          <div className="empty-state">
            <div className="icon">📭</div>
            <p>暂无持仓，生成信号后按建议操作</p>
          </div>
        ) : (
          <div className="table-container">
            <table>
              <thead>
                <tr>
                  <th>赛道</th>
                  <th>代码</th>
                  <th className="text-right">买入价</th>
                  <th className="text-right">份额</th>
                  <th className="text-right">成本</th>
                  <th className="text-right">天数</th>
                  <th className="text-right">备注</th>
                </tr>
              </thead>
              <tbody>
                {holdingsList.map((h: Record<string, unknown>) => (
                  <tr key={h.id as number}>
                    <td>{h.sector as string}</td>
                    <td style={{ fontFamily: 'var(--font-mono)' }}>{h.code as string}</td>
                    <td className="text-right">{Number(h.buy_price).toFixed(3)}</td>
                    <td className="text-right">{(h.shares as number).toLocaleString()}</td>
                    <td className="text-right">¥{Number(h.cost_amount).toLocaleString()}</td>
                    <td className="text-right">{h.hold_days as number ?? '—'}天</td>
                    <td className="text-right muted">{(h.notes as string) || '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
