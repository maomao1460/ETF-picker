// 作者：相空
import { useState, useMemo } from 'react';
import { api } from '../api/client';
import { useApi, useSubmit } from '../api/hooks';
import { exportCSV, today } from '../utils/export';

interface HoldingForm {
  sector: string;
  code: string;
  buy_date: string;
  buy_price: string;
  shares: string;
  notes: string;
}

const EMPTY_FORM: HoldingForm = {
  sector: '', code: '', buy_date: new Date().toISOString().slice(0, 10),
  buy_price: '', shares: '', notes: '',
};

/* ── P&L Calendar helpers ── */
interface DayPnl { date: string; pnl: number; pnl_pct: number; total_value: number }

function getDaysInMonth(year: number, month: number) {
  return new Date(year, month + 1, 0).getDate();
}

function getFirstDayOfWeek(year: number, month: number) {
  return new Date(year, month, 1).getDay(); // 0=Sun
}

function pnlColor(v: number): string {
  if (v > 0) return 'var(--green-bg)';
  if (v < 0) return 'var(--red-bg)';
  return 'transparent';
}

function pnlTextColor(v: number): string {
  if (v > 0) return 'var(--green)';
  if (v < 0) return 'var(--red)';
  return 'var(--text-muted)';
}

export default function Holdings() {
  const [tab, setTab] = useState<'active' | 'closed' | 'calendar'>('active');
  const [showNew, setShowNew] = useState(false);
  const [form, setForm] = useState(EMPTY_FORM);
  const [closeId, setCloseId] = useState<number | null>(null);
  const [closePrice, setClosePrice] = useState('');
  const [calMonth, setCalMonth] = useState(new Date().getMonth());
  const [calYear, setCalYear] = useState(new Date().getFullYear());

  const active = useApi(() => api.getHoldings(), [tab]);
  const closed = useApi(() => api.getClosedHoldings(), [tab]);
  const etfList = useApi(() => api.getEtfList(), []);
  const pnlData = useApi(() => api.getDailyPnl(12), [tab]);
  const { loading: submitting, error: submitErr, submit } = useSubmit();

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const _etfResp = etfList.data as any;
  const allEtfs = (_etfResp?.items ?? (Array.isArray(_etfResp) ? _etfResp : [])) as any[];
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const activeList = (active.data || []) as any[];
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const closedList = (closed.data || []) as any[];
  const pnlList = (pnlData.data || []) as DayPnl[];

  // Build date → pnl map
  const pnlMap = useMemo(() => {
    const m = new Map<string, DayPnl>();
    pnlList.forEach(d => m.set(d.date, d));
    return m;
  }, [pnlList]);

  // Calendar month stats
  const monthStats = useMemo(() => {
    const prefix = `${calYear}-${String(calMonth + 1).padStart(2, '0')}`;
    const days = pnlList.filter(d => d.date.startsWith(prefix));
    const total = days.reduce((s, d) => s + d.pnl, 0);
    const wins = days.filter(d => d.pnl > 0).length;
    return { total, wins, losses: days.filter(d => d.pnl < 0).length, count: days.length };
  }, [pnlList, calYear, calMonth]);

  const handleSectorChange = (sector: string) => {
    const etf = allEtfs.find((e: Record<string, unknown>) => e.sector === sector);
    setForm({ ...form, sector, code: etf?.code || '' });
  };

  const handleCreate = async () => {
    await submit(() => api.createHolding({
      sector: form.sector,
      code: form.code,
      buy_date: form.buy_date,
      buy_price: parseFloat(form.buy_price),
      shares: parseInt(form.shares),
      notes: form.notes || undefined,
    }));
    setShowNew(false);
    setForm(EMPTY_FORM);
    active.refetch();
  };

  const handleClose = async () => {
    if (closeId == null) return;
    await submit(() => api.closeHolding(closeId, {
      close_price: parseFloat(closePrice),
    }));
    setCloseId(null);
    setClosePrice('');
    active.refetch();
    closed.refetch();
  };

  const prevMonth = () => {
    if (calMonth === 0) { setCalYear(calYear - 1); setCalMonth(11); }
    else setCalMonth(calMonth - 1);
  };
  const nextMonth = () => {
    if (calMonth === 11) { setCalYear(calYear + 1); setCalMonth(0); }
    else setCalMonth(calMonth + 1);
  };

  return (
    <div>
      <div className="page-header">
        <div>
          <h2>💼 持仓管理</h2>
          <p className="subtitle">新增 · 编辑 · 平仓 · 历史记录</p>
        </div>
        <div style={{ display: 'flex', gap: 8 }}>
          <button className="btn btn-secondary" onClick={() => {
            const list = tab === 'active' ? activeList : closedList;
            if (tab === 'active') {
              exportCSV(`活跃持仓_${today()}.csv`,
                ['赛道', '代码', '买入价', '份额', '成本', '天数', '备注'],
                list.map((h: Record<string, unknown>) => [
                  h.sector as string, h.code as string,
                  Number(h.buy_price).toFixed(3), h.shares as number,
                  Number(h.cost_amount).toFixed(0), h.hold_days ?? '',
                  (h.notes as string) || '',
                ] as (string | number)[]));
            } else if (tab === 'closed') {
              exportCSV(`已平仓_${today()}.csv`,
                ['赛道', '代码', '买入价', '卖出价', '盈亏', '买入日', '平仓日'],
                list.map((h: Record<string, unknown>) => [
                  h.sector as string, h.code as string,
                  Number(h.buy_price).toFixed(3), Number(h.close_price).toFixed(3),
                  Number(h.close_pnl).toFixed(0), h.buy_date as string, h.close_date as string,
                ] as (string | number)[]));
            }
          }}>📥 导出CSV</button>
          <button className="btn btn-primary" onClick={() => setShowNew(true)}>
            + 新增持仓
          </button>
        </div>
      </div>

      {/* Tabs */}
      <div className="tabs">
        <button className={`tab ${tab === 'active' ? 'active' : ''}`} onClick={() => setTab('active')}>
          活跃持仓 ({activeList.length})
        </button>
        <button className={`tab ${tab === 'closed' ? 'active' : ''}`} onClick={() => setTab('closed')}>
          已平仓 ({closedList.length})
        </button>
        <button className={`tab ${tab === 'calendar' ? 'active' : ''}`} onClick={() => setTab('calendar')}>
          📅 P&L 日历
        </button>
      </div>

      {/* Active Holdings */}
      {tab === 'active' && (
        <div className="card">
          {activeList.length === 0 ? (
            <div className="empty-state">
              <div className="icon">📭</div>
              <p>暂无活跃持仓</p>
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
                    <th>备注</th>
                    <th className="text-right">操作</th>
                  </tr>
                </thead>
                <tbody>
                  {activeList.map((h: Record<string, unknown>) => (
                    <tr key={h.id as number}>
                      <td style={{ fontWeight: 600 }}>{h.sector as string}</td>
                      <td style={{ fontFamily: 'var(--font-mono)' }}>{h.code as string}</td>
                      <td className="text-right">{Number(h.buy_price).toFixed(3)}</td>
                      <td className="text-right">{(h.shares as number).toLocaleString()}</td>
                      <td className="text-right">¥{Number(h.cost_amount).toLocaleString()}</td>
                      <td className="text-right">{h.hold_days as number ?? '—'}天</td>
                      <td className="muted">{(h.notes as string) || '—'}</td>
                      <td className="text-right">
                        <button
                          className="btn btn-danger btn-sm"
                          onClick={() => { setCloseId(h.id as number); setClosePrice(''); }}
                        >
                          平仓
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {/* Closed Holdings */}
      {tab === 'closed' && (
        <div className="card">
          {closedList.length === 0 ? (
            <div className="empty-state">
              <div className="icon">📋</div>
              <p>暂无平仓记录</p>
            </div>
          ) : (
            <div className="table-container">
              <table>
                <thead>
                  <tr>
                    <th>赛道</th>
                    <th>代码</th>
                    <th className="text-right">买入价</th>
                    <th className="text-right">卖出价</th>
                    <th className="text-right">盈亏</th>
                    <th>买入日</th>
                    <th>平仓日</th>
                  </tr>
                </thead>
                <tbody>
                  {closedList.map((h: Record<string, unknown>) => (
                    <tr key={h.id as number}>
                      <td style={{ fontWeight: 600 }}>{h.sector as string}</td>
                      <td style={{ fontFamily: 'var(--font-mono)' }}>{h.code as string}</td>
                      <td className="text-right">{Number(h.buy_price).toFixed(3)}</td>
                      <td className="text-right">{Number(h.close_price).toFixed(3)}</td>
                      <td className={`text-right ${Number(h.close_pnl) >= 0 ? 'positive' : 'negative'}`}>
                        ¥{Number(h.close_pnl).toFixed(0)}
                      </td>
                      <td>{h.buy_date as string}</td>
                      <td>{h.close_date as string}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {/* P&L Calendar */}
      {tab === 'calendar' && (
        <div className="card">
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
            <button className="btn btn-ghost" onClick={prevMonth}>◀</button>
            <div style={{ textAlign: 'center' }}>
              <h3 style={{ fontSize: '1.1rem', fontWeight: 700 }}>
                {calYear}年{calMonth + 1}月
              </h3>
              <div style={{ fontSize: '0.82rem', color: 'var(--text-muted)', marginTop: 4 }}>
                月盈亏{' '}
                <span style={{ color: monthStats.total >= 0 ? 'var(--green)' : 'var(--red)', fontWeight: 600 }}>
                  ¥{monthStats.total.toFixed(0)}
                </span>
                {'  ·  '}胜 {monthStats.wins} 负 {monthStats.losses}
              </div>
            </div>
            <button className="btn btn-ghost" onClick={nextMonth}>▶</button>
          </div>

          {pnlData.loading && <div className="loading"><span className="spinner" /> 加载中...</div>}

          {!pnlData.loading && (
            <div className="calendar-grid">
              {['日', '一', '二', '三', '四', '五', '六'].map(d => (
                <div key={d} className="calendar-header">{d}</div>
              ))}
              {/* Empty cells for first row alignment */}
              {Array.from({ length: getFirstDayOfWeek(calYear, calMonth) }).map((_, i) => (
                <div key={`empty-${i}`} className="calendar-cell empty" />
              ))}
              {/* Day cells */}
              {Array.from({ length: getDaysInMonth(calYear, calMonth) }).map((_, idx) => {
                const day = idx + 1;
                const dateStr = `${calYear}-${String(calMonth + 1).padStart(2, '0')}-${String(day).padStart(2, '0')}`;
                const dp = pnlMap.get(dateStr);
                return (
                  <div key={day} className="calendar-cell" style={{ background: dp ? pnlColor(dp.pnl) : undefined }}
                    title={dp ? `¥${dp.pnl.toFixed(0)} (${dp.pnl_pct >= 0 ? '+' : ''}${dp.pnl_pct}%)` : ''}>
                    <span className="calendar-day">{day}</span>
                    {dp && (
                      <span className="calendar-pnl" style={{ color: pnlTextColor(dp.pnl) }}>
                        {dp.pnl >= 0 ? '+' : ''}{dp.pnl.toFixed(0)}
                      </span>
                    )}
                  </div>
                );
              })}
            </div>
          )}
        </div>
      )}

      {/* New Holding Modal */}
      {showNew && (
        <div className="modal-overlay" onClick={() => setShowNew(false)}>
          <div className="modal" onClick={e => e.stopPropagation()}>
            <h3>📥 新增持仓</h3>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
              <div className="form-group">
                <label className="form-label">赛道</label>
                <select className="form-select" value={form.sector} onChange={e => handleSectorChange(e.target.value)}>
                  <option value="">-- 选择赛道 --</option>
                  {allEtfs.map((e: Record<string, unknown>) => (
                    <option key={e.code as string} value={e.sector as string}>
                      {e.sector as string} ({e.code as string})
                    </option>
                  ))}
                </select>
              </div>
              <div className="form-row">
                <div className="form-group" style={{ flex: 1 }}>
                  <label className="form-label">买入日期</label>
                  <input className="form-input" type="date" value={form.buy_date}
                    onChange={e => setForm({ ...form, buy_date: e.target.value })} />
                </div>
                <div className="form-group" style={{ flex: 1 }}>
                  <label className="form-label">买入价</label>
                  <input className="form-input" type="number" step="0.001" placeholder="0.000"
                    value={form.buy_price} onChange={e => setForm({ ...form, buy_price: e.target.value })} />
                </div>
              </div>
              <div className="form-row">
                <div className="form-group" style={{ flex: 1 }}>
                  <label className="form-label">份额</label>
                  <input className="form-input" type="number" placeholder="1000"
                    value={form.shares} onChange={e => setForm({ ...form, shares: e.target.value })} />
                </div>
                <div className="form-group" style={{ flex: 1 }}>
                  <label className="form-label">备注</label>
                  <input className="form-input" placeholder="可选"
                    value={form.notes} onChange={e => setForm({ ...form, notes: e.target.value })} />
                </div>
              </div>
            </div>
            {submitErr && <p style={{ color: 'var(--red)', fontSize: '0.85rem', marginTop: 8 }}>❌ {submitErr}</p>}
            <div className="modal-actions">
              <button className="btn btn-secondary" onClick={() => setShowNew(false)}>取消</button>
              <button className="btn btn-primary" onClick={handleCreate} disabled={submitting || !form.sector}>
                {submitting ? '保存中...' : '保存'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Close Holding Modal */}
      {closeId != null && (
        <div className="modal-overlay" onClick={() => setCloseId(null)}>
          <div className="modal" onClick={e => e.stopPropagation()}>
            <h3>📤 确认平仓</h3>
            <div className="form-group" style={{ marginBottom: 12 }}>
              <label className="form-label">卖出价格</label>
              <input className="form-input" type="number" step="0.001" placeholder="请输入卖出价"
                value={closePrice} onChange={e => setClosePrice(e.target.value)} />
            </div>
            {submitErr && <p style={{ color: 'var(--red)', fontSize: '0.85rem' }}>❌ {submitErr}</p>}
            <div className="modal-actions">
              <button className="btn btn-secondary" onClick={() => setCloseId(null)}>取消</button>
              <button className="btn btn-danger" onClick={handleClose} disabled={submitting || !closePrice}>
                {submitting ? '处理中...' : '确认平仓'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
