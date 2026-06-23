// 作者：相空
const BASE_URL = 'http://localhost:8000/api';

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE_URL}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || `HTTP ${res.status}`);
  }
  return res.json();
}

export const api = {
  // Signal
  intradayCheck: (scoreMode = 'sector_rotation') =>
    request(`/signal/intraday-check?score_mode=${encodeURIComponent(scoreMode)}`),
  checkExits: (scoreMode = 'rank_momentum') =>
    request(`/signal/exit-check?score_mode=${encodeURIComponent(scoreMode)}`),
  generateSignal: (params: Record<string, unknown>) =>
    request('/signal/generate', { method: 'POST', body: JSON.stringify(params) }),
  getSignalHistory: (limit = 30) =>
    request<unknown[]>(`/signal/history?limit=${limit}`),
  getSignal: (id: number) =>
    request(`/signal/${id}`),

  // Holdings
  getHoldings: () => request<unknown[]>('/holdings'),
  createHolding: (data: Record<string, unknown>) =>
    request('/holdings', { method: 'POST', body: JSON.stringify(data) }),
  updateHolding: (id: number, data: Record<string, unknown>) =>
    request(`/holdings/${id}`, { method: 'PUT', body: JSON.stringify(data) }),
  closeHolding: (id: number, data: Record<string, unknown>) =>
    request(`/holdings/${id}/close`, { method: 'POST', body: JSON.stringify(data) }),
  getClosedHoldings: (limit = 50) =>
    request<unknown[]>(`/holdings/closed?limit=${limit}`),

  // Portfolio
  getPortfolioSummary: () => request('/portfolio/summary'),
  updateCash: (amount: number, reason: string) =>
    request('/portfolio/cash', { method: 'PUT', body: JSON.stringify({ amount, reason }) }),
  updateCapital: (amount: number) =>
    request('/portfolio/capital', { method: 'PUT', body: JSON.stringify({ initial_capital: amount }) }),
  getSnapshots: (days = 90) =>
    request<unknown[]>(`/portfolio/snapshots?days=${days}`),
  getPerformance: () => request('/portfolio/performance'),
  getDailyPnl: (months = 6) =>
    request<unknown[]>(`/portfolio/daily-pnl?months=${months}`),

  // ETF
  getEtfList: () =>
    request<unknown>('/etf/list'),
  getEtfChart: (code: string, days = 250) =>
    request(`/etf/${code}/chart?days=${days}`),

  // Market
  getMarketOverview: () => request('/market/overview'),
  refreshMarket: () => request('/market/refresh'),

  // Backtest
  runBacktest: (params: Record<string, unknown>) =>
    request('/backtest/run', { method: 'POST', body: JSON.stringify(params) }),
  getBacktestList: () =>
    request<unknown[]>('/backtest/list'),
  getBacktest: (id: number, fields?: string) =>
    request(`/backtest/${id}${fields ? `?fields=${fields}` : ''}`),
  deleteBacktest: (id: number) =>
    request(`/backtest/${id}`, { method: 'DELETE' }),
  scanBacktest: (params: Record<string, unknown>) =>
    request('/backtest/scan', { method: 'POST', body: JSON.stringify(params) }),
  scan2dBacktest: (params: Record<string, unknown>) =>
    request('/backtest/scan2d', { method: 'POST', body: JSON.stringify(params) }),
  walkForwardBacktest: (params: Record<string, unknown>) =>
    request('/backtest/walk-forward', { method: 'POST', body: JSON.stringify(params) }),
  drilldownBacktest: (params: Record<string, unknown>) =>
    request('/backtest/drilldown', { method: 'POST', body: JSON.stringify(params) }),
  getScanList: () =>
    request<unknown[]>('/backtest/scan/list'),
  getScan: (id: number) =>
    request(`/backtest/scan/${id}`),
  deleteScan: (id: number) =>
    request(`/backtest/scan/${id}`, { method: 'DELETE' }),
};
