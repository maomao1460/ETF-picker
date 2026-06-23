// 作者：相空
import { useState, useEffect, useRef } from 'react';
import {
  CartesianGrid,
  Label,
  Legend,
  Line,
  LineChart,
  ReferenceArea,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import { api } from '../api/client';
import { useApi, useSubmit } from '../api/hooks';
import { exportCSV, today } from '../utils/export';

type ScanValue = number | string | boolean;
type ScanKind = 'number' | 'enum' | 'boolean';
type ScanOption = { label: string; value: ScanValue };
type ScanParamMeta = {
  key: string;
  label: string;
  kind: ScanKind;
  defaults: ScanValue[];
  options?: ScanOption[];
};

const SCORE_MODE_OPTIONS: ScanOption[] = [
  { label: '排名动量', value: 'rank_momentum' },
  { label: '混合', value: 'mixed' },
  { label: '纯20日', value: 'pure20' },
  { label: '板块轮动(RPS+量比)', value: 'sector_rotation' },
];

const WEIGHT_SCHEME_OPTIONS: ScanOption[] = [
  { label: '等权', value: 'equal' },
  { label: '动量加权', value: 'momentum' },
  { label: '逆波动加权', value: 'inv_vol' },
];

const DEFAULTS = {
  name: '',
  backtest_years: 5,
  top_n: 5,
  rebal_days: 1,
  min_hold: 15,
  score_mode: 'rank_momentum',
  initial_capital: 100000,
  max_offensive: 4,
  weight_scheme: 'equal',
  ma200_risk: true,
  spike_filter: true,
  consec_down_exit: 3,
  rank_offset: 0,
};

// ── localStorage 键名 ──────────────────────────────────────────
const LS_PARAMS_KEY  = 'etf_bt_params';
const LS_FILL_KEY    = 'etf_bt_last_fill';
const LS_METRICS_KEY = 'etf_bt_baseline';

interface ParamFillInfo {
  from: '1D扫描' | '2D热力图' | 'Walk-forward';
  changedKeys: string[];
  metricLabel: string;
  time: string;
}

interface BaselineMetrics {
  annual_return: number;
  sharpe: number;
  max_drawdown: number;
  time: string;
}

const SCAN_PARAMS: ScanParamMeta[] = [
  { key: 'top_n', label: 'Top N', kind: 'number', defaults: [3, 4, 5, 6, 7, 8] },
  { key: 'min_hold', label: '最短持有天数', kind: 'number', defaults: [5, 10, 15, 20, 25, 30] },
  { key: 'rebal_days', label: '调仓周期', kind: 'number', defaults: [1, 3, 5, 7, 10] },
  { key: 'max_offensive', label: '进攻上限', kind: 'number', defaults: [2, 3, 4, 5, 6] },
  { key: 'consec_down_exit', label: '连跌退出天数', kind: 'number', defaults: [0, 2, 3, 4, 5] },
  { key: 'backtest_years', label: '回测年数', kind: 'number', defaults: [3, 4, 5, 6, 7] },
  { key: 'rank_offset', label: '排名偏移', kind: 'number', defaults: [0, 1, 2, 3] },
  { key: 'score_mode', label: '评分模式', kind: 'enum', defaults: ['rank_momentum', 'mixed', 'pure20'], options: SCORE_MODE_OPTIONS },
  { key: 'ma200_risk', label: 'MA200风控', kind: 'boolean', defaults: [true, false], options: [{ label: '开启', value: true }, { label: '关闭', value: false }] },
  { key: 'spike_filter', label: 'Spike过滤', kind: 'boolean', defaults: [true, false], options: [{ label: '开启', value: true }, { label: '关闭', value: false }] },
  { key: 'weight_scheme', label: '权重模式', kind: 'enum', defaults: ['equal', 'momentum', 'inv_vol'], options: WEIGHT_SCHEME_OPTIONS },
];

const SCAN_COLORS = [
  '#6c5ce7', '#00b894', '#e17055', '#fdcb6e', '#0984e3',
  '#d63031', '#00cec9', '#e84393', '#636e72', '#2d3436',
];

const HEAT_METRICS = [
  { key: 'annual_return', label: '年化收益', fmt: (v: number) => `${(v * 100).toFixed(1)}%`, higherBetter: true },
  { key: 'max_drawdown', label: '最大回撤', fmt: (v: number) => `${(v * 100).toFixed(1)}%`, higherBetter: false },
  { key: 'sharpe', label: '夏普比率', fmt: (v: number) => v.toFixed(2), higherBetter: true },
] as const;

const TRADE_STAT_ROWS = [
  { key: 'trade_win_rate', label: '交易胜率' },
  { key: 'up_day_ratio', label: '上涨日占比' },
  { key: 'round_trip_count', label: 'Round-trip 数' },
  { key: 'avg_hold_days', label: '平均持有天数' },
  { key: 'avg_win_pnl', label: '平均盈利' },
  { key: 'avg_loss_pnl', label: '平均亏损' },
  { key: 'avg_round_trip_return', label: '平均交易收益' },
  { key: 'profit_factor', label: 'Profit Factor' },
] as const;

function findParamMeta(key: string): ScanParamMeta {
  return SCAN_PARAMS.find(param => param.key === key) || SCAN_PARAMS[0];
}

function parseNumberValues(str: string): [number[], string | null] {
  const tokens = str.split(/[,，\s]+/).filter(Boolean);
  const values: number[] = [];
  for (const token of tokens) {
    const value = Number(token);
    if (!Number.isFinite(value)) {
      return [[], `"${token}" 不是有效数字`];
    }
    values.push(value);
  }
  return [values, null];
}

function valuesToText(values: ScanValue[]): string {
  return values.map(String).join(', ');
}

function formatScanValue(key: string, value: ScanValue): string {
  if (typeof value === 'boolean') return value ? '开启' : '关闭';
  if (key === 'score_mode') {
    return SCORE_MODE_OPTIONS.find(opt => opt.value === value)?.label || String(value);
  }
  if (key === 'weight_scheme') {
    if (value === 'pyramid') return 'Legacy: pyramid';
    if (value === 'top_heavy') return 'Legacy: top_heavy';
    return WEIGHT_SCHEME_OPTIONS.find(opt => opt.value === value)?.label || String(value);
  }
  return String(value);
}

function getWeightSchemeOptions(currentValue: string): ScanOption[] {
  const options = [...WEIGHT_SCHEME_OPTIONS];
  if (currentValue === 'pyramid' || currentValue === 'top_heavy') {
    options.push({ label: `Legacy: ${currentValue}`, value: currentValue });
  }
  return options;
}

// 基准参数展示配置（当前基准参数卡使用）
const PARAM_DISPLAY: Array<{ key: string; label: string; format: ((v: ScanValue) => string) | null }> = [
  { key: 'backtest_years', label: '回测年数', format: null },
  { key: 'top_n',          label: 'Top N',   format: null },
  { key: 'min_hold',       label: '最短持仓', format: null },
  { key: 'rebal_days',     label: '调仓周期', format: null },
  { key: 'score_mode',     label: '评分模式', format: (v) => formatScanValue('score_mode', v) },
  { key: 'max_offensive',  label: '攻防上限', format: null },
  { key: 'weight_scheme',  label: '权重模式', format: (v) => formatScanValue('weight_scheme', v) },
  { key: 'ma200_risk',     label: 'MA200',   format: (v) => (v ? '开' : '关') },
  { key: 'spike_filter',   label: 'Spike',   format: (v) => (v ? '开' : '关') },
  { key: 'consec_down_exit', label: '连跌退出', format: null },
];

function normalizeParams(raw?: Record<string, unknown>) {
  // 传入 raw 时（加载历史记录 / 重置）直接使用，跳过 localStorage
  if (raw) return { ...DEFAULTS, ...raw } as typeof DEFAULTS & Record<string, unknown>;
  // 否则从 localStorage 恢复上次保存的参数
  try {
    const saved = localStorage.getItem(LS_PARAMS_KEY);
    if (saved) return { ...DEFAULTS, ...JSON.parse(saved) } as typeof DEFAULTS & Record<string, unknown>;
  } catch { /* ignore */ }
  return { ...DEFAULTS } as typeof DEFAULTS & Record<string, unknown>;
}

function isSelectedValue(values: ScanValue[], value: ScanValue): boolean {
  return values.some(item => Object.is(item, value));
}

function toggleSelectedValue(values: ScanValue[], value: ScanValue): ScanValue[] {
  return isSelectedValue(values, value)
    ? values.filter(item => !Object.is(item, value))
    : [...values, value];
}

function pct(value: number | null | undefined): string {
  return value != null && Number.isFinite(value) ? `${(value * 100).toFixed(2)}%` : '—';
}

function num(value: number | null | undefined, digits = 2): string {
  return value != null && Number.isFinite(value) ? value.toFixed(digits) : '—';
}

function money(value: number | null | undefined): string {
  return value != null && Number.isFinite(value) ? `¥${value.toLocaleString(undefined, { maximumFractionDigits: 2 })}` : '—';
}

function formatMetricValue(key: string, value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return '—';
  if (['annual_return', 'total_return', 'max_drawdown', 'up_day_ratio', 'trade_win_rate', 'avg_round_trip_return'].includes(key)) {
    return pct(value);
  }
  if (['avg_win_pnl', 'avg_loss_pnl'].includes(key)) return money(value);
  if (key === 'avg_hold_days') return `${value.toFixed(1)} 天`;
  if (['sharpe', 'profit_factor'].includes(key)) return num(value, 2);
  if (['round_trip_count', 'n_trades'].includes(key)) return `${Math.round(value)}`;
  return num(value, 2);
}

function extractTradeStats(metrics?: Record<string, unknown>) {
  const source = metrics || {};
  return {
    trade_win_rate: source.trade_win_rate as number | undefined,
    up_day_ratio: (source.up_day_ratio ?? source.win_rate) as number | undefined,
    round_trip_count: source.round_trip_count as number | undefined,
    avg_hold_days: source.avg_hold_days as number | undefined,
    avg_win_pnl: source.avg_win_pnl as number | undefined,
    avg_loss_pnl: source.avg_loss_pnl as number | undefined,
    avg_round_trip_return: source.avg_round_trip_return as number | undefined,
    profit_factor: source.profit_factor as number | undefined,
  };
}

function toYearlyRows(raw: unknown): Array<{ year: string; return_pct: number }> {
  if (Array.isArray(raw)) return raw as Array<{ year: string; return_pct: number }>;
  return Object.entries((raw || {}) as Record<string, number>)
    .map(([year, returnPct]) => ({ year, return_pct: Number(returnPct) }))
    .sort((a, b) => Number(a.year) - Number(b.year));
}

function buildSeriesData(lines: Array<{ key: string; label: string; color: string; points: Array<{ date: string; value: number }> }>) {
  const dateMap: Record<string, Record<string, string | number>> = {};
  lines.forEach(line => {
    line.points.forEach(point => {
      if (!dateMap[point.date]) dateMap[point.date] = { date: point.date };
      dateMap[point.date][line.key] = point.value;
    });
  });
  return Object.values(dateMap).sort((a, b) => String(a.date).localeCompare(String(b.date)));
}

function getHeatColor(value: number, min: number, max: number, higherBetter: boolean) {
  if (!Number.isFinite(value) || !Number.isFinite(min) || !Number.isFinite(max)) return 'hsl(0,0%,40%)';
  if (max === min) return 'hsl(60,80%,55%)';
  let ratio = (value - min) / (max - min);
  if (!higherBetter) ratio = 1 - ratio;
  const hue = ratio * 130;
  return `hsl(${hue}, 80%, ${45 + ratio * 15}%)`;
}

function getNeighborValues(matrix: any[][], yi: number, xi: number, metric: string) {
  const neighbors: number[] = [];
  for (let dy = -1; dy <= 1; dy++) {
    for (let dx = -1; dx <= 1; dx++) {
      if (dy === 0 && dx === 0) continue;
      const row = matrix[yi + dy];
      const cell = row?.[xi + dx];
      const value = cell?.metrics?.[metric];
      if (typeof value === 'number' && Number.isFinite(value)) neighbors.push(value);
    }
  }
  return neighbors;
}

function classifyHeatCell(matrix: any[][], yi: number, xi: number, metric: string, higherBetter: boolean) {
  const cell = matrix[yi]?.[xi];
  const value = cell?.metrics?.[metric];
  if (typeof value !== 'number' || !Number.isFinite(value)) return '';
  const neighbors = getNeighborValues(matrix, yi, xi, metric);
  if (neighbors.length < 2) return '';
  const avg = neighbors.reduce((sum, item) => sum + item, 0) / neighbors.length;
  const std = Math.sqrt(neighbors.reduce((sum, item) => sum + (item - avg) ** 2, 0) / neighbors.length);
  if (std === 0) return value === avg ? 'plateau' : '';
  const diff = Math.abs(value - avg) / std;
  const isFlat = neighbors.every(item => Math.abs(item - avg) / std < 0.8);
  if (diff > 2.0 && ((higherBetter && value > avg) || (!higherBetter && value < avg))) return 'spike';
  if (isFlat && diff < 0.8) return 'plateau';
  return '';
}

function localDispersion(matrix: any[][], yi: number, xi: number, metric: string) {
  const neighbors = getNeighborValues(matrix, yi, xi, metric);
  if (neighbors.length < 2) return 999;
  const avg = neighbors.reduce((sum, item) => sum + item, 0) / neighbors.length;
  return Math.sqrt(neighbors.reduce((sum, item) => sum + (item - avg) ** 2, 0) / neighbors.length);
}

export default function Backtest() {
  const [params, setParams] = useState(normalizeParams());
  const [mode, setMode] = useState<'single' | 'scan' | 'scan2d' | 'walkforward'>('single');

  const [result, setResult] = useState<any>(null);
  const [tab, setTab] = useState<'equity' | 'yearly' | 'trades'>('equity');
  const list = useApi(() => api.getBacktestList(), [result]);
  const { loading, error, submit } = useSubmit();
  const runs = (list.data || []) as any[];

  const [scanParam, setScanParam] = useState(SCAN_PARAMS[0].key);
  const [scanValuesStr, setScanValuesStr] = useState(valuesToText(SCAN_PARAMS[0].defaults));
  const [scanOptionValues, setScanOptionValues] = useState<ScanValue[]>([]);
  const [scanResult, setScanResult] = useState<any>(null);
  const [scanValuesError, setScanValuesError] = useState<string | null>(null);
  const { loading: scanLoading, error: scanError, submit: scanSubmit } = useSubmit();

  const [scan2dResult, setScan2dResult] = useState<any>(null);
  const [scan2dXParam, setScan2dXParam] = useState(SCAN_PARAMS[0].key);
  const [scan2dYParam, setScan2dYParam] = useState(SCAN_PARAMS[1].key);
  const [scan2dXVals, setScan2dXVals] = useState(valuesToText(SCAN_PARAMS[0].defaults.slice(0, 6)));
  const [scan2dYVals, setScan2dYVals] = useState(valuesToText(SCAN_PARAMS[1].defaults.slice(0, 6)));
  const [scan2dXOptions, setScan2dXOptions] = useState<ScanValue[]>([]);
  const [scan2dYOptions, setScan2dYOptions] = useState<ScanValue[]>([]);
  const [scan2dValuesError, setScan2dValuesError] = useState<string | null>(null);
  const [heatMetric, setHeatMetric] = useState<typeof HEAT_METRICS[number]['key']>('annual_return');
  const { loading: scan2dLoading, error: scan2dError, submit: scan2dSubmit } = useSubmit();

  const [walkParam, setWalkParam] = useState(SCAN_PARAMS[0].key);
  const [walkValuesStr, setWalkValuesStr] = useState(valuesToText(SCAN_PARAMS[0].defaults));
  const [walkOptionValues, setWalkOptionValues] = useState<ScanValue[]>([]);
  const [walkValuesError, setWalkValuesError] = useState<string | null>(null);
  const [walkResult, setWalkResult] = useState<any>(null);
  const { loading: walkLoading, error: walkError, submit: walkSubmit } = useSubmit();

  // ── 持久化：参数来源 & 基准指标 ───────────────────────────────
  const [lastFill, setLastFill] = useState<ParamFillInfo | null>(() => {
    try { const s = localStorage.getItem(LS_FILL_KEY); return s ? JSON.parse(s) : null; } catch { return null; }
  });
  const [baselineMetrics, setBaselineMetrics] = useState<BaselineMetrics | null>(() => {
    try { const s = localStorage.getItem(LS_METRICS_KEY); return s ? JSON.parse(s) : null; } catch { return null; }
  });
  const [toast, setToast] = useState<string | null>(null);
  const toastTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const scanList = useApi(() => api.getScanList(), [scanResult, scan2dResult]);
  const scanRuns = (scanList.data || []) as any[];

  const [selectedHeatKey, setSelectedHeatKey] = useState<string | null>(null);
  const [selectedHeatLabel, setSelectedHeatLabel] = useState<string>('');
  const [drillCache, setDrillCache] = useState<Record<string, any>>({});
  const { loading: drilldownLoading, error: drilldownError, submit: drilldownSubmit } = useSubmit();

  const scanMeta = findParamMeta(scanParam);
  const scan2dXMeta = findParamMeta(scan2dXParam);
  const scan2dYMeta = findParamMeta(scan2dYParam);
  const walkMeta = findParamMeta(walkParam);

  // ── params 变化时持久化到 localStorage ───────────────────────
  useEffect(() => {
    // eslint-disable-next-line @typescript-eslint/no-unused-vars
    const { name: _name, ...toSave } = params as Record<string, unknown>;
    localStorage.setItem(LS_PARAMS_KEY, JSON.stringify(toSave));
  }, [params]);

  // ── Toast ────────────────────────────────────────────────────
  const showToast = (text: string) => {
    setToast(text);
    if (toastTimerRef.current) clearTimeout(toastTimerRef.current);
    toastTimerRef.current = setTimeout(() => setToast(null), 3500);
  };

  // ── 持久化 lastFill & baselineMetrics ────────────────────────
  const saveFill = (fill: ParamFillInfo) => {
    setLastFill(fill);
    localStorage.setItem(LS_FILL_KEY, JSON.stringify(fill));
  };

  const saveBaselineMetrics = (bm: BaselineMetrics) => {
    setBaselineMetrics(bm);
    localStorage.setItem(LS_METRICS_KEY, JSON.stringify(bm));
  };

  // ── 重置为默认参数 ────────────────────────────────────────────
  const handleResetParams = () => {
    setParams(normalizeParams(DEFAULTS));
    setLastFill(null);
    localStorage.removeItem(LS_PARAMS_KEY);
    localStorage.removeItem(LS_FILL_KEY);
    showToast('已重置为默认参数');
  };

  // ── 1D 扫描回填：按年化最高 ──────────────────────────────────
  const applyBestFromScan = (response: any) => {
    const results = ((response?.results || []) as any[]).filter((r: any) => r.metrics);
    if (!results.length) return;
    const best = results.reduce((prev: any, curr: any) =>
      curr.metrics.annual_return > prev.metrics.annual_return ? curr : prev
    );
    const sp = response.scan_param as string;
    setParams(prev => ({ ...prev, [sp]: best.value }));
    const fill: ParamFillInfo = {
      from: '1D扫描',
      changedKeys: [sp],
      metricLabel: `年化 ${(best.metrics.annual_return * 100).toFixed(1)}% · 夏普 ${best.metrics.sharpe?.toFixed(2) ?? '—'}`,
      time: new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' }),
    };
    saveFill(fill);
    showToast(`✅ 已回填最优参数：${findParamMeta(sp).label} = ${formatScanValue(sp, best.value)}（${fill.metricLabel}）`);
  };

  // ── 2D 热力图回填：按 Sharpe 最高 ───────────────────────────
  const applyBestFromScan2d = (response: any) => {
    const matrix  = (response?.matrix   || []) as any[][];
    const xVals   = (response?.x_values || []) as ScanValue[];
    const yVals   = (response?.y_values || []) as ScanValue[];
    const xParam  = (response?.x_param  || '') as string;
    const yParam  = (response?.y_param  || '') as string;
    if (!matrix.length || !xVals.length || !yVals.length || !xParam || !yParam) return;
    let bestVal = -Infinity, bestXi = 0, bestYi = 0;
    matrix.forEach((row, yi) =>
      row.forEach((cell: any, xi: number) => {
        const v = cell?.metrics?.sharpe;
        if (typeof v === 'number' && v > bestVal) { bestVal = v; bestXi = xi; bestYi = yi; }
      })
    );
    const xVal = xVals[bestXi];
    const yVal = yVals[bestYi];
    setParams(prev => ({ ...prev, [xParam]: xVal, [yParam]: yVal }));
    const fill: ParamFillInfo = {
      from: '2D热力图',
      changedKeys: [xParam, yParam],
      metricLabel: `夏普 ${bestVal.toFixed(2)}`,
      time: new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' }),
    };
    saveFill(fill);
    showToast(
      `✅ 已回填最优参数：${findParamMeta(xParam).label}=${formatScanValue(xParam, xVal)}，` +
      `${findParamMeta(yParam).label}=${formatScanValue(yParam, yVal)}（夏普 ${bestVal.toFixed(2)}）`
    );
  };

  // ── Walk-forward 回填：取命中次数最多的参数值 ────────────────
  const applyBestFromWalkForward = (response: any) => {
    const summary = (response?.chosen_values_summary || []) as Array<{ value: ScanValue; count: number }>;
    const sp = (response?.scan_param || '') as string;
    if (!summary.length || !sp) return;
    const best = summary[0];
    setParams(prev => ({ ...prev, [sp]: best.value }));
    const oosAnnual: number | null = response?.oos_metrics?.annual_return ?? null;
    const oosSharpe: number | null = response?.oos_metrics?.sharpe ?? null;
    const fill: ParamFillInfo = {
      from: 'Walk-forward',
      changedKeys: [sp],
      metricLabel:
        `OOS年化 ${oosAnnual != null ? (oosAnnual * 100).toFixed(1) + '%' : '—'}` +
        ` · OOS夏普 ${oosSharpe != null ? oosSharpe.toFixed(2) : '—'}`,
      time: new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' }),
    };
    saveFill(fill);
    showToast(
      `✅ 已回填 Walk-forward 最优参数：${findParamMeta(sp).label} = ` +
      `${formatScanValue(sp, best.value)}（命中 ${best.count} 次）`
    );
  };

  const resolveValues = (meta: ScanParamMeta, valueText: string, optionValues: ScanValue[]): [ScanValue[], string | null] => {
    if (meta.kind === 'number') {
      const [values, err] = parseNumberValues(valueText);
      if (err) return [[], err];
      return [values, null];
    }
    if (optionValues.length < 2) return [[], '至少需要 2 个扫描值'];
    return [optionValues, null];
  };

  const applyValueState = (
    meta: ScanParamMeta,
    values: ScanValue[],
    setText: (value: string) => void,
    setOptions: (value: ScanValue[]) => void,
  ) => {
    if (meta.kind === 'number') {
      setText(valuesToText(values));
      setOptions([]);
      return;
    }
    setOptions(values.length ? values : meta.defaults);
    setText(valuesToText(meta.defaults));
  };

  const handleRun = async () => {
    const response = await submit(() => api.runBacktest(params));
    if (response) {
      setResult(response);
      // 保存本次单测指标作为基准，供「当前基准参数」卡展示
      const m = response.metrics || {};
      if (m.annual_return != null) {
        saveBaselineMetrics({
          annual_return: m.annual_return,
          sharpe: m.sharpe ?? 0,
          max_drawdown: m.max_drawdown ?? 0,
          time: new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' }),
        });
      }
    }
  };

  const loadRun = async (id: number) => {
    const response: any = await submit(() => api.getBacktest(id));
    if (response) {
      if (response.params) setParams(normalizeParams(response.params));
      setLastFill(null); // 加载历史记录时清除回填标记
      setResult(response);
      setMode('single');
    }
  };

  const handleScan = async () => {
    const [values, err] = resolveValues(scanMeta, scanValuesStr, scanOptionValues);
    if (err) {
      setScanValuesError(err);
      return;
    }
    setScanValuesError(null);
    const response = await scanSubmit(() => api.scanBacktest({
      base_params: { ...params, name: undefined },
      scan_param: scanParam,
      scan_values: values,
    }));
    if (response) {
      setScanResult(response);
      applyBestFromScan(response); // 自动回填最优参数
    }
  };

  const handleScan2d = async () => {
    const [xValues, xErr] = resolveValues(scan2dXMeta, scan2dXVals, scan2dXOptions);
    if (xErr) {
      setScan2dValuesError(`X轴: ${xErr}`);
      return;
    }
    const [yValues, yErr] = resolveValues(scan2dYMeta, scan2dYVals, scan2dYOptions);
    if (yErr) {
      setScan2dValuesError(`Y轴: ${yErr}`);
      return;
    }
    setScan2dValuesError(null);
    const response = await scan2dSubmit(() => api.scan2dBacktest({
      base_params: { ...params, name: undefined },
      x_param: scan2dXParam,
      x_values: xValues,
      y_param: scan2dYParam,
      y_values: yValues,
    }));
    if (response) {
      setSelectedHeatKey(null);
      setSelectedHeatLabel('');
      setScan2dResult(response);
      applyBestFromScan2d(response); // 自动回填最优参数
    }
  };

  const handleWalkForward = async () => {
    const [values, err] = resolveValues(walkMeta, walkValuesStr, walkOptionValues);
    if (err) {
      setWalkValuesError(err);
      return;
    }
    setWalkValuesError(null);
    const response = await walkSubmit(() => api.walkForwardBacktest({
      base_params: { ...params, name: undefined },
      scan_param: walkParam,
      scan_values: values,
      objective: 'sharpe',
      window_mode: 'rolling',
      train_years: 3,
      test_years: 1,
    }));
    if (response) {
      setWalkResult(response);
      applyBestFromWalkForward(response); // 自动回填最优参数
    }
  };

  const loadScan = async (id: number) => {
    const response: any = await scanSubmit(() => api.getScan(id));
    if (!response) return;
    if (response.base_params) setParams(normalizeParams(response.base_params));
    if (response.scan_type === '2d') {
      setMode('scan2d');
      setSelectedHeatKey(null);
      setSelectedHeatLabel('');
      setScan2dResult(response);
      const xMeta = findParamMeta(response.x_param);
      const yMeta = findParamMeta(response.y_param);
      setScan2dXParam(response.x_param);
      setScan2dYParam(response.y_param);
      applyValueState(xMeta, (response.x_values || []) as ScanValue[], setScan2dXVals, setScan2dXOptions);
      applyValueState(yMeta, (response.y_values || []) as ScanValue[], setScan2dYVals, setScan2dYOptions);
      return;
    }
    setMode('scan');
    setScanResult(response);
    const meta = findParamMeta(response.scan_param);
    setScanParam(response.scan_param);
    const values = Array.isArray(response.results) ? response.results.map((item: { value: ScanValue }) => item.value) : [];
    applyValueState(meta, values, setScanValuesStr, setScanOptionValues);
  };

  const handleHeatCellClick = async (cell: { x: ScanValue; y: ScanValue }) => {
    const key = `${heatMetric}:${JSON.stringify(cell.x)}:${JSON.stringify(cell.y)}`;
    const label = `${findParamMeta(scan2dXParam).label}=${formatScanValue(scan2dXParam, cell.x)}，${findParamMeta(scan2dYParam).label}=${formatScanValue(scan2dYParam, cell.y)}`;
    setSelectedHeatKey(key);
    setSelectedHeatLabel(label);
    if (drillCache[key]) return;
    const response = await drilldownSubmit(() => api.drilldownBacktest({
      base_params: { ...(scan2dResult?.base_params || params), name: undefined },
      overrides: { [scan2dXParam]: cell.x, [scan2dYParam]: cell.y },
    }));
    if (response) {
      setDrillCache(prev => ({ ...prev, [key]: response }));
    }
  };

  const m = (result?.metrics || {}) as Record<string, number>;
  const tradeStats = (result?.trade_stats || extractTradeStats(m)) as Record<string, number>;
  const equity = (result?.equity_curve || []) as Array<Record<string, unknown>>;
  const yearlyRows = toYearlyRows(result?.yearly);
  const tradeRows = (result?.trades || []) as any[];
  const eqData = equity.map(point => ({
    date: String(point.date).slice(2),
    value: Number(point.value) / 10000,
  }));

  const ddStart = m.max_dd_start ? String(m.max_dd_start).slice(2) : null;
  const ddEnd = m.max_dd_end ? String(m.max_dd_end).slice(2) : null;
  const ddRecovery = m.max_dd_recovery ? String(m.max_dd_recovery).slice(2) : null;
  const ddRecoveryDays = m.max_dd_recovery_days as number | null;

  const scanResults = (scanResult?.results || []) as any[];
  const validScanResults = scanResults.filter(row => row.metrics);
  const scanChartData = buildSeriesData(validScanResults.map((row, idx) => ({
    key: `v${idx}`,
    label: `${scanResult?.scan_param}=${formatScanValue(scanResult?.scan_param || '', row.value)}`,
    color: SCAN_COLORS[idx % SCAN_COLORS.length],
    points: ((row.equity_curve || []) as Array<Record<string, unknown>>).map(point => ({
      date: String(point.date).slice(2),
      value: Number(point.value) / 10000,
    })),
  })));
  const bestIdx = validScanResults.length > 0
    ? validScanResults.reduce((best, row, idx) =>
        row.metrics.annual_return > validScanResults[best].metrics.annual_return ? idx : best, 0)
    : -1;

  const heatMatrix: any[][] = scan2dResult?.matrix || [];
  const heatXVals: ScanValue[] = (scan2dResult?.x_values || []) as ScanValue[];
  const heatYVals: ScanValue[] = (scan2dResult?.y_values || []) as ScanValue[];
  const heatXParam: string = scan2dResult?.x_param || '';
  const heatYParam: string = scan2dResult?.y_param || '';
  const heatMetricMeta = HEAT_METRICS.find(metric => metric.key === heatMetric)!;
  const heatAllValues = heatMatrix
    .flat()
    .map(cell => cell?.metrics?.[heatMetric])
    .filter((value: unknown) => typeof value === 'number' && Number.isFinite(value)) as number[];
  const heatMin = heatAllValues.length > 0 ? Math.min(...heatAllValues) : 0;
  const heatMax = heatAllValues.length > 0 ? Math.max(...heatAllValues) : 0;

  const stablePool = heatMatrix.flatMap((row, yi) =>
    row.flatMap((cell: any, xi: number) => {
      if (!cell?.metrics) return [];
      const cls = classifyHeatCell(heatMatrix, yi, xi, heatMetric, heatMetricMeta.higherBetter);
      const dispersion = localDispersion(heatMatrix, yi, xi, heatMetric);
      return [{ yi, xi, cell, cls, dispersion, value: cell.metrics[heatMetric] as number }];
    }),
  );
  const plateauCells = stablePool.filter(item => item.cls === 'plateau');
  const smoothCells = stablePool.filter(item => item.cls !== 'spike');
  const topStableCells = [...plateauCells, ...smoothCells.filter(item => item.cls !== 'plateau')]
    .sort((a, b) => {
      if (heatMetricMeta.higherBetter) {
        if (b.value !== a.value) return b.value - a.value;
      } else if (a.value !== b.value) {
        return a.value - b.value;
      }
      return a.dispersion - b.dispersion;
    })
    .slice(0, 5);

  const activeDrilldown = selectedHeatKey ? drillCache[selectedHeatKey] : null;
  const drilldownCurveData = activeDrilldown ? buildSeriesData([
    {
      key: 'baseline',
      label: '基线',
      color: '#636e72',
      points: ((activeDrilldown.baseline?.equity_curve || []) as Array<Record<string, unknown>>).map(point => ({
        date: String(point.date).slice(2),
        value: Number(point.value) / 10000,
      })),
    },
    {
      key: 'selected',
      label: '选中参数',
      color: '#6c5ce7',
      points: ((activeDrilldown.selected?.equity_curve || []) as Array<Record<string, unknown>>).map(point => ({
        date: String(point.date).slice(2),
        value: Number(point.value) / 10000,
      })),
    },
  ]) : [];
  const drilldownYearly = activeDrilldown ? Array.from(new Set([
    ...Object.keys(activeDrilldown.baseline?.yearly || {}),
    ...Object.keys(activeDrilldown.selected?.yearly || {}),
  ])).sort() : [];

  const walkMetrics = (walkResult?.oos_metrics || {}) as Record<string, number>;
  const walkTradeStats = (walkResult?.oos_trade_stats || extractTradeStats(walkMetrics)) as Record<string, number>;
  const walkCurveData = ((walkResult?.oos_equity_curve || []) as Array<Record<string, unknown>>).map(point => ({
    date: String(point.date).slice(2),
    value: Number(point.value) / 10000,
  }));
  const walkWindows = (walkResult?.windows || []) as any[];

  const renderValueSelector = (
    meta: ScanParamMeta,
    values: ScanValue[],
    setValues: (values: ScanValue[]) => void,
  ) => (
    <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginTop: 6 }}>
      {(meta.options || []).map(option => {
        const active = isSelectedValue(values, option.value);
        return (
          <button
            key={`${meta.key}-${String(option.value)}`}
            type="button"
            className={`btn ${active ? 'btn-primary' : 'btn-secondary'}`}
            style={{ padding: '6px 10px', fontSize: '0.8rem' }}
            onClick={() => setValues(toggleSelectedValue(values, option.value))}
          >
            {option.label}
          </button>
        );
      })}
    </div>
  );

  return (
    <div>
      {/* ── Toast 通知 ─────────────────────────────────────────── */}
      {toast && (
        <div style={{
          position: 'fixed', bottom: 28, right: 28, zIndex: 1000,
          background: '#1a1d2e', border: '1px solid #2a2d3e',
          borderLeft: '3px solid #00b894',
          borderRadius: 8, padding: '10px 18px',
          fontSize: '0.85rem', color: 'var(--text-primary)',
          boxShadow: '0 4px 16px rgba(0,0,0,0.4)',
          maxWidth: 480,
        }}>
          {toast}
        </div>
      )}

      <div className="page-header">
        <div>
          <h2>🧪 回测中心</h2>
          <p className="subtitle">单次回测 · 参数扫描 · 二维热力图 · Walk-forward</p>
        </div>
      </div>

      <div style={{ display: 'flex', gap: 8, marginBottom: 16, flexWrap: 'wrap' }}>
        <button className={`btn ${mode === 'single' ? 'btn-primary' : 'btn-secondary'}`} onClick={() => setMode('single')}>▶ 单次回测</button>
        <button className={`btn ${mode === 'scan' ? 'btn-primary' : 'btn-secondary'}`} onClick={() => setMode('scan')}>📊 参数扫描</button>
        <button className={`btn ${mode === 'scan2d' ? 'btn-primary' : 'btn-secondary'}`} onClick={() => setMode('scan2d')}>🗺️ 二维热力图</button>
        <button className={`btn ${mode === 'walkforward' ? 'btn-primary' : 'btn-secondary'}`} onClick={() => setMode('walkforward')}>🪟 Walk-forward</button>
      </div>

      {/* ── 当前基准参数卡 ─────────────────────────────────────── */}
      <div className="card" style={{ marginBottom: 12, padding: '12px 16px' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 10 }}>
          <span style={{ fontWeight: 600, fontSize: '0.88rem', color: 'var(--text-primary)' }}>
            📌 当前基准参数
          </span>
          <div style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
            {/* 上次单次回测指标 */}
            {baselineMetrics && (
              <span style={{ fontSize: '0.78rem', color: 'var(--text-muted)' }}>
                上次单测（{baselineMetrics.time}）：
                年化&nbsp;<b style={{ color: baselineMetrics.annual_return >= 0 ? '#22c55e' : '#ef4444' }}>
                  {(baselineMetrics.annual_return * 100).toFixed(2)}%
                </b>
                &nbsp;·&nbsp;夏普&nbsp;<b style={{ color: baselineMetrics.sharpe >= 1 ? '#22c55e' : baselineMetrics.sharpe >= 0 ? '#eab308' : '#ef4444' }}>
                  {baselineMetrics.sharpe.toFixed(2)}
                </b>
                &nbsp;·&nbsp;回撤&nbsp;<b style={{ color: '#ef4444' }}>
                  {(baselineMetrics.max_drawdown * 100).toFixed(2)}%
                </b>
              </span>
            )}
            <button
              className="btn btn-secondary btn-sm"
              style={{ fontSize: '0.75rem', padding: '3px 10px' }}
              onClick={handleResetParams}
              title="清除所有已保存参数，恢复系统默认值"
            >
              重置默认
            </button>
          </div>
        </div>

        {/* 参数徽章列表 */}
        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'flex-start' }}>
          {PARAM_DISPLAY.map(({ key, label, format }) => {
            const value = (params as Record<string, unknown>)[key];
            const isAutoFilled = lastFill?.changedKeys.includes(key) ?? false;
            return (
              <div key={key} style={{
                display: 'flex', flexDirection: 'column', alignItems: 'center',
                background: isAutoFilled ? 'rgba(108,92,231,0.15)' : 'rgba(255,255,255,0.05)',
                border: `1px solid ${isAutoFilled ? 'rgba(162,155,254,0.45)' : 'var(--border-subtle)'}`,
                borderRadius: 6, padding: '4px 10px', minWidth: 58, textAlign: 'center',
                transition: 'all 0.2s',
              }}>
                <span style={{ fontSize: '0.68rem', color: 'var(--text-muted)', marginBottom: 2 }}>{label}</span>
                <span style={{
                  fontSize: '0.9rem', fontWeight: 700,
                  color: isAutoFilled ? '#a29bfe' : 'var(--text-primary)',
                }}>
                  {format ? format(value as ScanValue) : String(value)}
                </span>
                {isAutoFilled && (
                  <span style={{ fontSize: '0.62rem', color: '#a29bfe', marginTop: 2, whiteSpace: 'nowrap' }}>
                    ↑ {lastFill!.from}
                  </span>
                )}
              </div>
            );
          })}
          {/* 回填来源说明 */}
          {lastFill && (
            <div style={{
              alignSelf: 'center', marginLeft: 6,
              fontSize: '0.75rem', color: 'var(--text-muted)', lineHeight: 1.5,
            }}>
              <div style={{ color: '#a29bfe', fontWeight: 600 }}>
                {lastFill.from} 回填 · {lastFill.time}
              </div>
              <div>{lastFill.metricLabel}</div>
            </div>
          )}
        </div>
      </div>

      <div className="params-panel">
        <div className="card-header"><span className="card-title">⚙️ 回测参数</span></div>
        <div className="params-grid">
          <div className="form-group">
            <label className="form-label">回测年数</label>
            <input className="form-input" type="number" value={params.backtest_years as number}
              onChange={e => setParams({ ...params, backtest_years: +e.target.value })} />
          </div>
          <div className="form-group">
            <label className="form-label">Top N</label>
            <select className="form-select" value={params.top_n as number}
              onChange={e => setParams({ ...params, top_n: +e.target.value })}>
              {[3, 4, 5, 6, 7, 8].map(value => <option key={value} value={value}>{value}</option>)}
            </select>
          </div>
          <div className="form-group">
            <label className="form-label">调仓周期</label>
            <input className="form-input" type="number" value={params.rebal_days as number}
              onChange={e => setParams({ ...params, rebal_days: +e.target.value })} />
          </div>
          <div className="form-group">
            <label className="form-label">最短持有</label>
            <input className="form-input" type="number" value={params.min_hold as number}
              onChange={e => setParams({ ...params, min_hold: +e.target.value })} />
          </div>
          <div className="form-group">
            <label className="form-label">评分模式</label>
            <select className="form-select" value={String(params.score_mode)}
              onChange={e => setParams({ ...params, score_mode: e.target.value })}>
              {SCORE_MODE_OPTIONS.map(option => <option key={String(option.value)} value={String(option.value)}>{option.label}</option>)}
            </select>
          </div>
          <div className="form-group">
            <label className="form-label">初始资金</label>
            <input className="form-input" type="number" value={params.initial_capital as number}
              onChange={e => setParams({ ...params, initial_capital: +e.target.value })} />
          </div>
          <div className="form-group">
            <label className="form-label">进攻上限</label>
            <input className="form-input" type="number" value={params.max_offensive as number}
              onChange={e => setParams({ ...params, max_offensive: +e.target.value })} />
          </div>
          <div className="form-group">
            <label className="form-label">连跌退出</label>
            <input className="form-input" type="number" value={params.consec_down_exit as number}
              onChange={e => setParams({ ...params, consec_down_exit: +e.target.value })} />
          </div>
          <div className="form-group">
            <label className="form-label">排名偏移</label>
            <input className="form-input" type="number" value={params.rank_offset as number}
              onChange={e => setParams({ ...params, rank_offset: +e.target.value })} />
          </div>
          <div className="form-group">
            <label className="form-label">权重模式</label>
            <select className="form-select" value={String(params.weight_scheme)}
              onChange={e => setParams({ ...params, weight_scheme: e.target.value })}>
              {getWeightSchemeOptions(String(params.weight_scheme)).map(option => (
                <option key={String(option.value)} value={String(option.value)}>{option.label}</option>
              ))}
            </select>
          </div>
        </div>
        <div style={{ display: 'flex', gap: 16, marginTop: 12, flexWrap: 'wrap' }}>
          <label className="form-checkbox">
            <input type="checkbox" checked={Boolean(params.ma200_risk)}
              onChange={e => setParams({ ...params, ma200_risk: e.target.checked })} /> MA200风控
          </label>
          <label className="form-checkbox">
            <input type="checkbox" checked={Boolean(params.spike_filter)}
              onChange={e => setParams({ ...params, spike_filter: e.target.checked })} /> Spike过滤
          </label>
        </div>

        {mode === 'single' && (
          <div className="params-actions">
            <div className="form-group" style={{ flex: 1 }}>
              <input className="form-input" placeholder="回测名称 (可选)" value={String(params.name || '')}
                onChange={e => setParams({ ...params, name: e.target.value })} />
            </div>
            <button className="btn btn-primary btn-lg" onClick={handleRun} disabled={loading}>
              {loading ? <><span className="spinner" /> 回测中...</> : '▶ 运行回测'}
            </button>
          </div>
        )}

        {mode === 'scan' && (
          <div style={{ marginTop: 16, borderTop: '1px solid var(--border-subtle)', paddingTop: 16 }}>
            <div className="card-header" style={{ marginBottom: 12 }}>
              <span className="card-title">🔍 扫描配置</span>
              <span className="card-subtitle">统一支持数值、枚举和布尔参数</span>
            </div>
            <div style={{ display: 'flex', gap: 12, alignItems: 'flex-end', flexWrap: 'wrap' }}>
              <div className="form-group">
                <label className="form-label">扫描参数</label>
                <select className="form-select" value={scanParam}
                  onChange={e => {
                    const meta = findParamMeta(e.target.value);
                    setScanParam(meta.key);
                    applyValueState(meta, meta.defaults, setScanValuesStr, setScanOptionValues);
                  }}>
                  {SCAN_PARAMS.map(param => <option key={param.key} value={param.key}>{param.label} ({param.key})</option>)}
                </select>
              </div>
              <div className="form-group" style={{ flex: 1, minWidth: 240 }}>
                <label className="form-label">{scanMeta.kind === 'number' ? '扫描值 (逗号分隔, 最多10个)' : '扫描值 (至少选择2个)'}</label>
                {scanMeta.kind === 'number' ? (
                  <input className="form-input" value={scanValuesStr} onChange={e => setScanValuesStr(e.target.value)} placeholder="如: 3, 5, 7, 10" />
                ) : (
                  <>
                    {renderValueSelector(scanMeta, scanOptionValues, setScanOptionValues)}
                    <div style={{ marginTop: 6, fontSize: '0.8rem', color: 'var(--text-muted)' }}>
                      已选: {scanOptionValues.map(value => formatScanValue(scanMeta.key, value)).join(' / ') || '—'}
                    </div>
                  </>
                )}
                {scanValuesError && <p style={{ color: 'var(--red)', fontSize: '0.8rem', margin: '4px 0 0' }}>❌ {scanValuesError}</p>}
              </div>
              <button className="btn btn-primary btn-lg" onClick={handleScan} disabled={scanLoading}>
                {scanLoading ? <><span className="spinner" /> 扫描中...</> : '📊 开始扫描'}
              </button>
            </div>
          </div>
        )}

        {mode === 'scan2d' && (
          <div style={{ marginTop: 16, borderTop: '1px solid var(--border-subtle)', paddingTop: 16 }}>
            <div className="card-header" style={{ marginBottom: 12 }}>
              <span className="card-title">🗺️ 二维扫描配置</span>
              <span className="card-subtitle">任意两类参数交叉扫描，点击格子查看 drill-down</span>
            </div>
            <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', alignItems: 'flex-end' }}>
              <div className="form-group">
                <label className="form-label">X轴参数</label>
                <select className="form-select" value={scan2dXParam}
                  onChange={e => {
                    const meta = findParamMeta(e.target.value);
                    const nextY = scan2dYParam === meta.key ? SCAN_PARAMS.find(param => param.key !== meta.key)?.key || meta.key : scan2dYParam;
                    setScan2dXParam(meta.key);
                    applyValueState(meta, meta.defaults, setScan2dXVals, setScan2dXOptions);
                    if (nextY !== scan2dYParam) {
                      const yMeta = findParamMeta(nextY);
                      setScan2dYParam(nextY);
                      applyValueState(yMeta, yMeta.defaults, setScan2dYVals, setScan2dYOptions);
                    }
                  }}>
                  {SCAN_PARAMS.map(param => <option key={param.key} value={param.key}>{param.label}</option>)}
                </select>
              </div>
              <div className="form-group" style={{ flex: 1, minWidth: 180 }}>
                <label className="form-label">{scan2dXMeta.kind === 'number' ? 'X值 (逗号分隔, ≤8)' : 'X值 (至少选择2个)'}</label>
                {scan2dXMeta.kind === 'number' ? (
                  <input className="form-input" value={scan2dXVals} onChange={e => setScan2dXVals(e.target.value)} placeholder="如: 3, 5, 7" />
                ) : (
                  <>
                    {renderValueSelector(scan2dXMeta, scan2dXOptions, setScan2dXOptions)}
                    <div style={{ marginTop: 6, fontSize: '0.8rem', color: 'var(--text-muted)' }}>
                      已选: {scan2dXOptions.map(value => formatScanValue(scan2dXMeta.key, value)).join(' / ') || '—'}
                    </div>
                  </>
                )}
              </div>
              <div className="form-group">
                <label className="form-label">Y轴参数</label>
                <select className="form-select" value={scan2dYParam}
                  onChange={e => {
                    const meta = findParamMeta(e.target.value);
                    setScan2dYParam(meta.key);
                    applyValueState(meta, meta.defaults, setScan2dYVals, setScan2dYOptions);
                  }}>
                  {SCAN_PARAMS.filter(param => param.key !== scan2dXParam).map(param => <option key={param.key} value={param.key}>{param.label}</option>)}
                </select>
              </div>
              <div className="form-group" style={{ flex: 1, minWidth: 180 }}>
                <label className="form-label">{scan2dYMeta.kind === 'number' ? 'Y值 (逗号分隔, ≤8)' : 'Y值 (至少选择2个)'}</label>
                {scan2dYMeta.kind === 'number' ? (
                  <input className="form-input" value={scan2dYVals} onChange={e => setScan2dYVals(e.target.value)} placeholder="如: 5, 10, 15" />
                ) : (
                  <>
                    {renderValueSelector(scan2dYMeta, scan2dYOptions, setScan2dYOptions)}
                    <div style={{ marginTop: 6, fontSize: '0.8rem', color: 'var(--text-muted)' }}>
                      已选: {scan2dYOptions.map(value => formatScanValue(scan2dYMeta.key, value)).join(' / ') || '—'}
                    </div>
                  </>
                )}
              </div>
              <button className="btn btn-primary btn-lg" onClick={handleScan2d} disabled={scan2dLoading}>
                {scan2dLoading ? <><span className="spinner" /> 扫描中...</> : '🗺️ 生成热力图'}
              </button>
            </div>
            {scan2dValuesError && <p style={{ color: 'var(--red)', marginTop: 8, fontSize: '0.85rem' }}>❌ {scan2dValuesError}</p>}
          </div>
        )}

        {mode === 'walkforward' && (
          <div style={{ marginTop: 16, borderTop: '1px solid var(--border-subtle)', paddingTop: 16 }}>
            <div className="card-header" style={{ marginBottom: 12 }}>
              <span className="card-title">🪟 Walk-forward 配置</span>
              <span className="card-subtitle">滚动优化验证 — 防止参数过拟合，评估策略真实泛化能力</span>
            </div>

            {/* ── 方法说明 ── */}
            <div style={{
              background: 'rgba(9,132,227,0.07)', border: '1px solid rgba(9,132,227,0.2)',
              borderRadius: 8, padding: '12px 16px', marginBottom: 14, fontSize: '0.82rem',
              color: 'var(--text-secondary)', lineHeight: 1.7,
            }}>
              <div style={{ fontWeight: 600, color: 'var(--text-primary)', marginBottom: 6 }}>📐 工作原理（3 步循环）</div>
              <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
                <span style={{ background: 'rgba(9,132,227,0.15)', borderRadius: 4, padding: '2px 8px' }}>
                  ① 训练窗 (3年)
                </span>
                <span style={{ color: 'var(--text-muted)' }}>→</span>
                <span>对所有候选参数值跑回测，按 <b>Sharpe 最优</b>选出最佳参数</span>
              </div>
              <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap', marginTop: 4 }}>
                <span style={{ background: 'rgba(0,206,201,0.15)', borderRadius: 4, padding: '2px 8px' }}>
                  ② 测试窗 (1年)
                </span>
                <span style={{ color: 'var(--text-muted)' }}>→</span>
                <span>用训练窗选出的参数在<b>从未见过的数据</b>上跑一次，记录真实表现</span>
              </div>
              <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap', marginTop: 4 }}>
                <span style={{ background: 'rgba(108,92,231,0.15)', borderRadius: 4, padding: '2px 8px' }}>
                  ③ 窗口滚动
                </span>
                <span style={{ color: 'var(--text-muted)' }}>→</span>
                <span>测试窗向前移动 1 年，重复以上步骤，最终把所有测试窗<b>首尾拼接</b>为完整 OOS 净值</span>
              </div>
              <div style={{ marginTop: 8, color: 'var(--text-muted)', fontSize: '0.78rem' }}>
                💡 OOS（Out-of-Sample）指标比普通回测更可信，因为参数不是在测试数据上调出来的
              </div>
            </div>

            <div style={{ display: 'flex', gap: 12, alignItems: 'flex-end', flexWrap: 'wrap' }}>
              <div className="form-group">
                <label className="form-label">优化参数 <span style={{ color: 'var(--text-muted)', fontSize: '0.75rem' }}>（每个窗口滚动寻优的目标）</span></label>
                <select className="form-select" value={walkParam}
                  onChange={e => {
                    const meta = findParamMeta(e.target.value);
                    setWalkParam(meta.key);
                    applyValueState(meta, meta.defaults, setWalkValuesStr, setWalkOptionValues);
                  }}>
                  {SCAN_PARAMS.map(param => <option key={param.key} value={param.key}>{param.label}</option>)}
                </select>
              </div>
              <div className="form-group" style={{ flex: 1, minWidth: 240 }}>
                <label className="form-label">{walkMeta.kind === 'number' ? '候选值 (逗号分隔)' : '候选值 (至少选择2个)'}</label>
                {walkMeta.kind === 'number' ? (
                  <input className="form-input" value={walkValuesStr} onChange={e => setWalkValuesStr(e.target.value)} placeholder="如: 3, 5, 7" />
                ) : (
                  <>
                    {renderValueSelector(walkMeta, walkOptionValues, setWalkOptionValues)}
                    <div style={{ marginTop: 6, fontSize: '0.8rem', color: 'var(--text-muted)' }}>
                      已选: {walkOptionValues.map(value => formatScanValue(walkMeta.key, value)).join(' / ') || '—'}
                    </div>
                  </>
                )}
                {walkValuesError && <p style={{ color: 'var(--red)', fontSize: '0.8rem', margin: '4px 0 0' }}>❌ {walkValuesError}</p>}
              </div>
              <button className="btn btn-primary btn-lg" onClick={handleWalkForward} disabled={walkLoading}>
                {walkLoading ? <><span className="spinner" /> 运行中...</> : '🪟 开始 Walk-forward'}
              </button>
            </div>
          </div>
        )}

        {error && <p style={{ color: 'var(--red)', marginTop: 8, fontSize: '0.85rem' }}>❌ {error}</p>}
        {scanError && <p style={{ color: 'var(--red)', marginTop: 8, fontSize: '0.85rem' }}>❌ {scanError}</p>}
        {scan2dError && <p style={{ color: 'var(--red)', marginTop: 8, fontSize: '0.85rem' }}>❌ {scan2dError}</p>}
        {walkError && <p style={{ color: 'var(--red)', marginTop: 8, fontSize: '0.85rem' }}>❌ {walkError}</p>}
      </div>

      {mode === 'single' && Object.keys(m).length > 0 && (
        <>
          <div className="stat-grid">
            <div className="stat-card green">
              <div className="stat-label">累计收益</div>
              <div className="stat-value">{pct(m.total_return)}</div>
            </div>
            <div className="stat-card blue">
              <div className="stat-label">年化收益</div>
              <div className="stat-value">{pct(m.annual_return)}</div>
            </div>
            <div className="stat-card red">
              <div className="stat-label">最大回撤</div>
              <div className="stat-value">{pct(m.max_drawdown)}</div>
            </div>
            <div className="stat-card accent">
              <div className="stat-label">夏普比率</div>
              <div className="stat-value">{num(m.sharpe, 2)}</div>
            </div>
            <div className="stat-card yellow">
              <div className="stat-label">交易胜率</div>
              <div className="stat-value">{pct(tradeStats.trade_win_rate)}</div>
            </div>
            <div className="stat-card" style={{ borderLeft: '3px solid #00cec9' }}>
              <div className="stat-label">上涨日占比</div>
              <div className="stat-value">{pct(tradeStats.up_day_ratio)}</div>
            </div>
            <div className="stat-card" style={{ borderLeft: '3px solid #e84393' }}>
              <div className="stat-label">Round-trip 数</div>
              <div className="stat-value">{tradeStats.round_trip_count ?? '—'}</div>
            </div>
            <div className="stat-card" style={{ borderLeft: '3px solid #fdcb6e' }}>
              <div className="stat-label">平均持有</div>
              <div className="stat-value">{tradeStats.avg_hold_days != null ? `${tradeStats.avg_hold_days.toFixed(1)} 天` : '—'}</div>
            </div>
            <div className="stat-card" style={{ borderLeft: '3px solid #6c5ce7' }}>
              <div className="stat-label">Profit Factor</div>
              <div className="stat-value">{num(tradeStats.profit_factor, 2)}</div>
            </div>
            <div className="stat-card" style={{ borderLeft: '3px solid #e17055' }}>
              <div className="stat-label">回撤修复</div>
              <div className="stat-value" style={{ fontSize: '1.1rem' }}>
                {ddRecoveryDays != null ? `${ddRecoveryDays} 天` : '未修复'}
              </div>
              {ddStart && ddEnd && (
                <div style={{ fontSize: '0.7rem', color: 'var(--text-muted)', marginTop: 2 }}>
                  {String(m.max_dd_start).slice(5)} → {String(m.max_dd_end).slice(5)}
                  {ddRecovery && ` → ${String(m.max_dd_recovery).slice(5)}`}
                </div>
              )}
            </div>
          </div>

          <div className="card" style={{ marginBottom: 20 }}>
            <div className="card-header">
              <span className="card-title">📌 交易统计</span>
            </div>
            <div className="table-container">
              <table>
                <thead><tr><th>指标</th><th className="text-right">值</th></tr></thead>
                <tbody>
                  {TRADE_STAT_ROWS.map(row => (
                    <tr key={row.key}>
                      <td>{row.label}</td>
                      <td className="text-right">{formatMetricValue(row.key, tradeStats[row.key] as number | undefined)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          <div className="tabs">
            <button className={`tab ${tab === 'equity' ? 'active' : ''}`} onClick={() => setTab('equity')}>净值曲线</button>
            <button className={`tab ${tab === 'yearly' ? 'active' : ''}`} onClick={() => setTab('yearly')}>年度明细</button>
            <button className={`tab ${tab === 'trades' ? 'active' : ''}`} onClick={() => setTab('trades')}>交易记录</button>
          </div>

          {tab === 'equity' && eqData.length > 0 && (
            <div className="card" style={{ marginBottom: 20 }}>
              {ddStart && ddEnd && (
                <div style={{ display: 'flex', gap: 24, padding: '12px 16px 0', fontSize: '0.82rem', color: 'var(--text-secondary)', flexWrap: 'wrap' }}>
                  <span style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                    <span style={{ display: 'inline-block', width: 14, height: 3, background: '#00b894', borderRadius: 2 }} />
                    最大回撤 <b style={{ color: '#e17055' }}>{pct(m.max_drawdown)}</b>
                  </span>
                  <span style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                    <span style={{ display: 'inline-block', width: 14, height: 14, background: 'rgba(232,67,147,0.15)', borderRadius: 3, border: '1px solid rgba(232,67,147,0.3)' }} />
                    最大回撤修复天数 <b>{ddRecoveryDays != null ? `${ddRecoveryDays}天` : '未修复'}</b>
                  </span>
                </div>
              )}
              <div style={{ height: 360 }}>
                <ResponsiveContainer>
                  <LineChart data={eqData} margin={{ top: 10, right: 10, bottom: 0, left: 10 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="var(--border-subtle)" />
                    <XAxis dataKey="date" interval={Math.max(Math.floor(eqData.length / 8), 0)} tick={{ fontSize: 11 }} />
                    <YAxis tick={{ fontSize: 11 }} />
                    <Tooltip contentStyle={{ background: '#1c1e2e', border: '1px solid #2a2d3e', borderRadius: 8, fontSize: 12 }} />
                    {ddStart && (ddRecovery || ddEnd) && (
                      <ReferenceArea x1={ddStart} x2={ddRecovery ?? ddEnd ?? undefined} fill="rgba(232,67,147,0.12)" stroke="rgba(232,67,147,0.25)" strokeDasharray="4 3" />
                    )}
                    {ddEnd && (
                      <ReferenceLine x={ddEnd} stroke="#e84393" strokeDasharray="3 3" strokeWidth={1.5}>
                        <Label value={`最大回撤${pct(m.max_drawdown)}`} position="insideBottomLeft" fill="#e84393" fontSize={11} fontWeight={600} offset={6} />
                      </ReferenceLine>
                    )}
                    {ddRecovery && ddRecoveryDays != null && (
                      <ReferenceLine x={ddRecovery} stroke="#00b894" strokeDasharray="3 3" strokeWidth={1.5}>
                        <Label value={`${ddRecoveryDays}天修复`} position="insideTopLeft" fill="#00b894" fontSize={11} fontWeight={600} offset={6} />
                      </ReferenceLine>
                    )}
                    <Line type="monotone" dataKey="value" stroke="#6c5ce7" strokeWidth={2} dot={false} name="净值(万)" />
                  </LineChart>
                </ResponsiveContainer>
              </div>
            </div>
          )}

          {tab === 'yearly' && yearlyRows.length > 0 && (
            <div className="card">
              <div className="table-container">
                <table>
                  <thead><tr><th>年份</th><th className="text-right">收益率</th></tr></thead>
                  <tbody>
                    {yearlyRows.map((row, idx) => (
                      <tr key={idx}>
                        <td>{row.year}</td>
                        <td className={`text-right ${row.return_pct >= 0 ? 'positive' : 'negative'}`}>{pct(row.return_pct)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {tab === 'trades' && tradeRows.length > 0 && (
            <div className="card">
              <div className="table-container">
                <table>
                  <thead><tr><th>日期</th><th>赛道</th><th>操作</th><th className="text-right">股数</th><th className="text-right">价格</th><th className="text-right">金额</th><th className="text-right">持有天数</th><th className="text-right">盈亏</th></tr></thead>
                  <caption style={{ captionSide: 'top', textAlign: 'right', padding: '0 0 8px' }}>
                    <button className="btn btn-secondary btn-sm" onClick={() => {
                      exportCSV(
                        `回测交易_${today()}.csv`,
                        ['日期', '赛道', '操作', '股数', '价格', '金额', '持有天数', '盈亏'],
                        tradeRows.map((trade: Record<string, unknown>) => [
                          String(trade.date),
                          String(trade.sector),
                          String(trade.action),
                          Number(trade.shares),
                          trade.price != null ? Number(trade.price).toFixed(3) : '',
                          trade.amount != null ? Number(trade.amount).toFixed(2) : '',
                          Number(trade.hold_days ?? 0),
                          trade.pnl != null ? Number(trade.pnl).toFixed(2) : '',
                        ]),
                      );
                    }}>📥 导出CSV</button>
                  </caption>
                  <tbody>
                    {tradeRows.slice(0, 100).map((trade: Record<string, unknown>, idx: number) => (
                      <tr key={idx}>
                        <td>{String(trade.date)}</td>
                        <td>{String(trade.sector)}</td>
                        <td>{String(trade.action)}</td>
                        <td className="text-right">{typeof trade.shares === 'number' ? trade.shares.toLocaleString() : '—'}</td>
                        <td className="text-right">{trade.price != null ? Number(trade.price).toFixed(3) : '—'}</td>
                        <td className="text-right">{trade.amount != null ? `¥${Number(trade.amount).toLocaleString()}` : '—'}</td>
                        <td className="text-right">{trade.hold_days != null ? String(trade.hold_days) : '—'}</td>
                        <td className={`text-right ${Number(trade.pnl) >= 0 ? 'positive' : 'negative'}`}>
                          {trade.pnl != null ? `¥${Number(trade.pnl).toLocaleString()}` : '—'}
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

      {mode === 'scan' && validScanResults.length > 0 && (
        <>
          <div className="card" style={{ marginBottom: 20 }}>
            <div className="card-header">
              <span className="card-title">📊 参数对比 — {findParamMeta(scanResult.scan_param).label}</span>
              <button className="btn btn-secondary btn-sm" onClick={() => {
                const headers = ['指标', ...validScanResults.map(row => `${scanResult.scan_param}=${formatScanValue(scanResult.scan_param, row.value)}`)];
                const rows = [
                  { key: 'annual_return', label: '年化收益' },
                  { key: 'total_return', label: '累计收益' },
                  { key: 'max_drawdown', label: '最大回撤' },
                  { key: 'sharpe', label: '夏普比率' },
                  { key: 'trade_win_rate', label: '交易胜率' },
                  { key: 'up_day_ratio', label: '上涨日占比' },
                  { key: 'round_trip_count', label: 'Round-trip 数' },
                  { key: 'avg_hold_days', label: '平均持有天数' },
                  { key: 'profit_factor', label: 'Profit Factor' },
                  { key: 'n_trades', label: '交易次数' },
                ].map(metric => [
                  metric.label,
                  ...validScanResults.map(row => formatMetricValue(metric.key, row.metrics[metric.key])),
                ]);
                exportCSV(`参数扫描_${scanResult.scan_param}_${today()}.csv`, headers, rows);
              }}>📥 导出CSV</button>
            </div>
            <div className="table-container">
              <table>
                <thead>
                  <tr>
                    <th>指标</th>
                    {validScanResults.map((row, idx) => (
                      <th key={idx} className={`text-right ${idx === bestIdx ? 'positive' : ''}`} style={idx === bestIdx ? { background: 'rgba(0,184,148,0.12)' } : {}}>
                        {findParamMeta(scanResult.scan_param).label}={formatScanValue(scanResult.scan_param, row.value)}
                        {idx === bestIdx && ' ⭐'}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {[
                    { key: 'annual_return', label: '年化收益' },
                    { key: 'total_return', label: '累计收益' },
                    { key: 'max_drawdown', label: '最大回撤' },
                    { key: 'sharpe', label: '夏普比率' },
                    { key: 'trade_win_rate', label: '交易胜率' },
                    { key: 'up_day_ratio', label: '上涨日占比' },
                    { key: 'round_trip_count', label: 'Round-trip 数' },
                    { key: 'avg_hold_days', label: '平均持有天数' },
                    { key: 'profit_factor', label: 'Profit Factor' },
                    { key: 'n_trades', label: '交易次数' },
                  ].map(metric => (
                    <tr key={metric.key}>
                      <td>{metric.label}</td>
                      {validScanResults.map((row, idx) => (
                        <td key={idx} className="text-right" style={idx === bestIdx ? { background: 'rgba(0,184,148,0.08)' } : {}}>
                          {formatMetricValue(metric.key, row.metrics[metric.key])}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          {scanChartData.length > 0 && (
            <div className="card" style={{ marginBottom: 20 }}>
              <div className="card-header">
                <span className="card-title">📈 叠加净值曲线</span>
                <span className="card-subtitle">不同参数值的净值走势对比 (万元)</span>
              </div>
              <div style={{ height: 400 }}>
                <ResponsiveContainer>
                  <LineChart data={scanChartData} margin={{ top: 10, right: 10, bottom: 0, left: 10 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="var(--border-subtle)" />
                    <XAxis dataKey="date" interval={Math.max(Math.floor(scanChartData.length / 8), 0)} tick={{ fontSize: 11 }} />
                    <YAxis tick={{ fontSize: 11 }} />
                    <Tooltip contentStyle={{ background: '#1c1e2e', border: '1px solid #2a2d3e', borderRadius: 8, fontSize: 12 }} formatter={(value) => [Number(value).toFixed(2) + '万', '']} />
                    <Legend />
                    {validScanResults.map((row, idx) => (
                      <Line
                        key={idx}
                        type="monotone"
                        dataKey={`v${idx}`}
                        stroke={SCAN_COLORS[idx % SCAN_COLORS.length]}
                        strokeWidth={idx === bestIdx ? 3 : 1.5}
                        dot={false}
                        name={`${findParamMeta(scanResult.scan_param).label}=${formatScanValue(scanResult.scan_param, row.value)}`}
                        opacity={idx === bestIdx ? 1 : 0.75}
                      />
                    ))}
                  </LineChart>
                </ResponsiveContainer>
              </div>
            </div>
          )}
        </>
      )}

      {mode === 'scan2d' && heatMatrix.length > 0 && (
        <>
          <div className="card" style={{ marginTop: 20 }}>
            <div className="card-header">
              <span className="card-title">🗺️ 参数热力图</span>
              <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                <select className="form-select" style={{ width: 'auto', fontSize: '0.85rem' }} value={heatMetric}
                  onChange={e => setHeatMetric(e.target.value as typeof heatMetric)}>
                  {HEAT_METRICS.map(metric => <option key={metric.key} value={metric.key}>{metric.label}</option>)}
                </select>
                <button className="btn btn-secondary btn-sm" onClick={() => {
                  const headers = ['Y \\ X', ...heatXVals.map(value => formatScanValue(heatXParam, value))];
                  const rows = heatMatrix.map((row, yi) => [
                    formatScanValue(heatYParam, heatYVals[yi]),
                    ...row.map((cell: { metrics?: Record<string, number> }) => cell.metrics ? heatMetricMeta.fmt(cell.metrics[heatMetric]) : 'ERR'),
                  ]);
                  exportCSV(`heatmap_${heatXParam}_${heatYParam}_${today()}.csv`, headers, rows);
                }}>📥 CSV</button>
              </div>
            </div>
            <div style={{ display: 'flex', gap: 16, padding: '8px 16px', fontSize: '0.8rem', color: 'var(--text-secondary)', flexWrap: 'wrap', alignItems: 'center' }}>
              <span>🟢 高原 = 稳定区域</span>
              <span>⚠️ 尖峰 = 过拟合风险</span>
              <span style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
                颜色:
                <span style={{
                  display: 'inline-block',
                  width: 60,
                  height: 12,
                  borderRadius: 3,
                  background: heatMetricMeta.higherBetter
                    ? 'linear-gradient(to right, hsl(0,80%,45%), hsl(60,80%,55%), hsl(130,80%,60%))'
                    : 'linear-gradient(to right, hsl(130,80%,60%), hsl(60,80%,55%), hsl(0,80%,45%))',
                }} />
                {heatMetricMeta.higherBetter ? ' 差→好' : ' 好→差'}
              </span>
            </div>
            <div style={{ padding: '8px 16px 16px', overflowX: 'auto' }}>
              <div style={{ textAlign: 'center', fontSize: '0.85rem', fontWeight: 600, marginBottom: 4, color: 'var(--text-secondary)' }}>
                X: {findParamMeta(heatXParam).label}
              </div>
              <div style={{ display: 'grid', gridTemplateColumns: `80px repeat(${heatXVals.length}, minmax(72px, 1fr))`, gap: 2 }}>
                <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)', textAlign: 'center', padding: 4, fontWeight: 700 }}>
                  {findParamMeta(heatYParam).label.slice(0, 4) || 'Y'}
                </div>
                {heatXVals.map((value, idx) => (
                  <div key={idx} style={{ textAlign: 'center', fontSize: '0.8rem', fontWeight: 600, padding: 4, color: 'var(--text-secondary)' }}>
                    {formatScanValue(heatXParam, value)}
                  </div>
                ))}
                {heatMatrix.map((row, yi) => ([
                  <div key={`y-${yi}`} style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: '0.8rem', fontWeight: 600, color: 'var(--text-secondary)' }}>
                    {formatScanValue(heatYParam, heatYVals[yi])}
                  </div>,
                  ...row.map((cell: any, xi: number) => {
                    if (!cell.metrics) {
                      return <div key={`${yi}-${xi}`} style={{ background: 'var(--bg-tertiary)', borderRadius: 6, display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 8, fontSize: '0.75rem', color: 'var(--text-muted)' }}>ERR</div>;
                    }
                    const value = cell.metrics[heatMetric] as number;
                    const cls = classifyHeatCell(heatMatrix, yi, xi, heatMetric, heatMetricMeta.higherBetter);
                    const key = `${heatMetric}:${JSON.stringify(cell.x)}:${JSON.stringify(cell.y)}`;
                    return (
                      <button
                        key={`${yi}-${xi}`}
                        type="button"
                        onClick={() => handleHeatCellClick(cell)}
                        title={[
                          `${findParamMeta(heatXParam).label}=${formatScanValue(heatXParam, cell.x)}`,
                          `${findParamMeta(heatYParam).label}=${formatScanValue(heatYParam, cell.y)}`,
                          `年化: ${(cell.metrics.annual_return * 100).toFixed(1)}%`,
                          `回撤: ${(cell.metrics.max_drawdown * 100).toFixed(1)}%`,
                          `夏普: ${num(cell.metrics.sharpe, 2)}`,
                        ].join('\n')}
                        style={{
                          background: getHeatColor(value, heatMin, heatMax, heatMetricMeta.higherBetter),
                          borderRadius: 6,
                          display: 'flex',
                          flexDirection: 'column',
                          alignItems: 'center',
                          justifyContent: 'center',
                          padding: '8px 4px',
                          border: selectedHeatKey === key
                            ? '2px solid rgba(255,255,255,0.95)'
                            : cls === 'plateau'
                              ? '2px solid rgba(0,255,100,0.6)'
                              : cls === 'spike'
                                ? '2px dashed rgba(255,165,0,0.8)'
                                : '1px solid rgba(255,255,255,0.08)',
                          minHeight: 56,
                          cursor: 'pointer',
                          position: 'relative',
                        }}
                      >
                        <span style={{ fontSize: '0.86rem', fontWeight: 700, color: '#fff', textShadow: '0 1px 3px rgba(0,0,0,0.5)' }}>
                          {heatMetricMeta.fmt(value)}
                        </span>
                        {cls === 'spike' && <span style={{ position: 'absolute', top: 2, right: 4, fontSize: '0.7rem' }}>⚠️</span>}
                        {cls === 'plateau' && <span style={{ position: 'absolute', top: 2, right: 4, fontSize: '0.7rem' }}>🟢</span>}
                      </button>
                    );
                  }),
                ]))}
              </div>
              <div style={{ textAlign: 'left', fontSize: '0.85rem', fontWeight: 600, marginTop: 4, color: 'var(--text-secondary)' }}>
                Y: {findParamMeta(heatYParam).label}
              </div>
            </div>
          </div>

          <div className="card" style={{ marginTop: 20 }}>
            <div className="card-header">
              <span className="card-title">🟢 稳定区 Top 5</span>
              <span className="card-subtitle">优先 plateau，不足时补平滑高分格子</span>
            </div>
            <div className="table-container">
              <table>
                <thead><tr><th>#</th><th>参数组合</th><th className="text-right">{heatMetricMeta.label}</th><th>判定</th></tr></thead>
                <tbody>
                  {topStableCells.map((item, idx) => (
                    <tr key={idx}>
                      <td>{idx + 1}</td>
                      <td>
                        <button
                          type="button"
                          className="btn btn-secondary btn-sm"
                          onClick={() => handleHeatCellClick(item.cell)}
                          style={{ padding: '4px 8px' }}
                        >
                          {findParamMeta(heatXParam).label}={formatScanValue(heatXParam, item.cell.x)} / {findParamMeta(heatYParam).label}={formatScanValue(heatYParam, item.cell.y)}
                        </button>
                      </td>
                      <td className="text-right">{heatMetricMeta.fmt(item.value)}</td>
                      <td>{item.cls === 'plateau' ? '高原' : '平滑高分'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          {drilldownError && <p style={{ color: 'var(--red)', marginTop: 8, fontSize: '0.85rem' }}>❌ {drilldownError}</p>}
          {selectedHeatKey && (
            <div className="card" style={{ marginTop: 20 }}>
              <div className="card-header">
                <span className="card-title">🔎 热力图 Drill-down</span>
                <span className="card-subtitle">{selectedHeatLabel}</span>
              </div>
              {drilldownLoading && !activeDrilldown && <div className="loading"><span className="spinner" /></div>}
              {activeDrilldown && (
                <>
                  <div className="stat-grid">
                    <div className="stat-card blue">
                      <div className="stat-label">选中年化</div>
                      <div className="stat-value">{pct(activeDrilldown.selected?.metrics?.annual_return)}</div>
                    </div>
                    <div className="stat-card green">
                      <div className="stat-label">基线年化</div>
                      <div className="stat-value">{pct(activeDrilldown.baseline?.metrics?.annual_return)}</div>
                    </div>
                    <div className="stat-card red">
                      <div className="stat-label">年化差值</div>
                      <div className="stat-value">{pct(activeDrilldown.diff?.annual_return)}</div>
                    </div>
                    <div className="stat-card accent">
                      <div className="stat-label">夏普差值</div>
                      <div className="stat-value">{num(activeDrilldown.diff?.sharpe, 2)}</div>
                    </div>
                  </div>

                  {drilldownCurveData.length > 0 && (
                    <div style={{ height: 320, marginBottom: 20 }}>
                      <ResponsiveContainer>
                        <LineChart data={drilldownCurveData} margin={{ top: 10, right: 10, bottom: 0, left: 10 }}>
                          <CartesianGrid strokeDasharray="3 3" stroke="var(--border-subtle)" />
                          <XAxis dataKey="date" interval={Math.max(Math.floor(drilldownCurveData.length / 8), 0)} tick={{ fontSize: 11 }} />
                          <YAxis tick={{ fontSize: 11 }} />
                          <Tooltip contentStyle={{ background: '#1c1e2e', border: '1px solid #2a2d3e', borderRadius: 8, fontSize: 12 }} formatter={(value) => [Number(value).toFixed(2) + '万', '']} />
                          <Legend />
                          <Line type="monotone" dataKey="baseline" stroke="#636e72" strokeWidth={2} dot={false} name="基线" />
                          <Line type="monotone" dataKey="selected" stroke="#6c5ce7" strokeWidth={2.2} dot={false} name="选中参数" />
                        </LineChart>
                      </ResponsiveContainer>
                    </div>
                  )}

                  <div style={{ display: 'grid', gridTemplateColumns: '1.1fr 1fr', gap: 16 }}>
                    <div className="table-container">
                      <table>
                        <thead><tr><th>年份</th><th className="text-right">基线</th><th className="text-right">选中</th></tr></thead>
                        <tbody>
                          {drilldownYearly.map(year => (
                            <tr key={year}>
                              <td>{year}</td>
                              <td className="text-right">{pct(activeDrilldown.baseline?.yearly?.[year])}</td>
                              <td className="text-right">{pct(activeDrilldown.selected?.yearly?.[year])}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                    <div className="table-container">
                      <table>
                        <thead><tr><th>交易统计</th><th className="text-right">基线</th><th className="text-right">选中</th><th className="text-right">差值</th></tr></thead>
                        <tbody>
                          {TRADE_STAT_ROWS.map(row => (
                            <tr key={row.key}>
                              <td>{row.label}</td>
                              <td className="text-right">{formatMetricValue(row.key, activeDrilldown.baseline?.trade_stats?.[row.key] ?? activeDrilldown.baseline?.metrics?.[row.key])}</td>
                              <td className="text-right">{formatMetricValue(row.key, activeDrilldown.selected?.trade_stats?.[row.key] ?? activeDrilldown.selected?.metrics?.[row.key])}</td>
                              <td className="text-right">{formatMetricValue(row.key, activeDrilldown.diff?.[row.key])}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </div>
                </>
              )}
            </div>
          )}
        </>
      )}

      {mode === 'walkforward' && walkResult && (
        <>
          {/* ── OOS 说明横幅 ── */}
          <div style={{
            background: 'rgba(0,206,201,0.07)', border: '1px solid rgba(0,206,201,0.2)',
            borderRadius: 8, padding: '10px 16px', marginBottom: 14,
            fontSize: '0.8rem', color: 'var(--text-secondary)',
            display: 'flex', alignItems: 'center', gap: 8,
          }}>
            <span style={{ fontSize: '1rem' }}>📊</span>
            <span>以下均为 <b>OOS（样本外）</b>拼接指标 —— 每段测试窗所用参数来自各自训练窗的寻优结果，非事后拟合，可信度高于普通回测</span>
          </div>

          <div className="stat-grid">
            <div className="stat-card green">
              <div className="stat-label">OOS 年化收益</div>
              <div className="stat-value">{pct(walkMetrics.annual_return)}</div>
              <div style={{ fontSize: '0.72rem', color: 'var(--text-muted)', marginTop: 4 }}>所有测试窗拼接后的等效年化，越高越好</div>
            </div>
            <div className="stat-card blue">
              <div className="stat-label">OOS 累计收益</div>
              <div className="stat-value">{pct(walkMetrics.total_return)}</div>
              <div style={{ fontSize: '0.72rem', color: 'var(--text-muted)', marginTop: 4 }}>全部测试窗净值首尾相乘的总涨幅</div>
            </div>
            <div className="stat-card red">
              <div className="stat-label">OOS 最大回撤</div>
              <div className="stat-value">{pct(walkMetrics.max_drawdown)}</div>
              <div style={{ fontSize: '0.72rem', color: 'var(--text-muted)', marginTop: 4 }}>测试窗内最大峰谷跌幅，越小越好（&lt;20% 可接受）</div>
            </div>
            <div className="stat-card accent">
              <div className="stat-label">OOS 夏普比率</div>
              <div className="stat-value">{num(walkMetrics.sharpe, 2)}</div>
              <div style={{ fontSize: '0.72rem', color: 'var(--text-muted)', marginTop: 4 }}>收益 / 波动比，&gt;1 可接受，&gt;1.5 优秀，&gt;2 极佳</div>
            </div>
            <div className="stat-card yellow">
              <div className="stat-label">OOS 交易胜率</div>
              <div className="stat-value">{pct(walkTradeStats.trade_win_rate)}</div>
              <div style={{ fontSize: '0.72rem', color: 'var(--text-muted)', marginTop: 4 }}>盈利笔数 / 总交易笔数，轮动策略 50%+ 属正常</div>
            </div>
            <div className="stat-card" style={{ borderLeft: '3px solid #00cec9' }}>
              <div className="stat-label">参数命中分布</div>
              <div className="stat-value" style={{ fontSize: '0.95rem' }}>{(walkResult.chosen_values_summary || []).map((item: any) => `${item.value}×${item.count}`).join(' / ') || '—'}</div>
              <div style={{ fontSize: '0.72rem', color: 'var(--text-muted)', marginTop: 4 }}>各候选值被选中次数；分布越集中说明该参数越稳定</div>
            </div>
          </div>

          {walkCurveData.length > 0 && (
            <div className="card" style={{ marginBottom: 20 }}>
              <div className="card-header">
                <span className="card-title">📈 OOS 拼接净值曲线</span>
                <span className="card-subtitle">滚动训练窗选参后的测试窗净值表现 (万元)</span>
              </div>
              <div style={{ height: 360 }}>
                <ResponsiveContainer>
                  <LineChart data={walkCurveData} margin={{ top: 10, right: 10, bottom: 0, left: 10 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="var(--border-subtle)" />
                    <XAxis dataKey="date" interval={Math.max(Math.floor(walkCurveData.length / 8), 0)} tick={{ fontSize: 11 }} />
                    <YAxis tick={{ fontSize: 11 }} />
                    <Tooltip contentStyle={{ background: '#1c1e2e', border: '1px solid #2a2d3e', borderRadius: 8, fontSize: 12 }} formatter={(value) => [Number(value).toFixed(2) + '万', '']} />
                    <Line type="monotone" dataKey="value" stroke="#0984e3" strokeWidth={2.2} dot={false} name="OOS 净值(万)" />
                  </LineChart>
                </ResponsiveContainer>
              </div>
            </div>
          )}

          <div className="card">
            <div className="card-header">
              <span className="card-title">🗓️ 窗口明细</span>
              <span className="card-subtitle">
                {walkResult.window_mode === 'rolling' ? '滚动训练窗' : walkResult.window_mode}
                &nbsp;·&nbsp;{walkWindows.length} 个窗口
                &nbsp;·&nbsp;训练窗在已知数据上寻优，测试窗为真实 OOS 表现
              </span>
            </div>
            {/* 图例说明 */}
            <div style={{
              display: 'flex', gap: 16, padding: '8px 12px',
              fontSize: '0.75rem', color: 'var(--text-muted)',
              borderBottom: '1px solid var(--border-subtle)', flexWrap: 'wrap',
            }}>
              <span>🔵 <b>训练夏普</b>：训练窗内最优参数的样本内夏普，反映策略在历史数据上的拟合质量</span>
              <span>🟢 <b>测试年化 / 测试夏普</b>：该参数在测试窗（未见数据）的真实表现，<b>这才是关键</b></span>
              <span style={{ color: '#ef4444' }}>⚠️ 若测试夏普远低于训练夏普，说明该窗口存在过拟合</span>
            </div>
            <div className="table-container">
              <table>
                <thead>
                  <tr>
                    <th title="用于参数寻优的历史数据区间（已知数据）">训练窗 <span style={{ color: 'var(--text-muted)', fontWeight: 400 }}>↑寻优用</span></th>
                    <th title="用选出参数真实跑一次的验证区间（未知数据）">测试窗 <span style={{ color: 'var(--text-muted)', fontWeight: 400 }}>↑OOS验证</span></th>
                    <th title="训练窗中 Sharpe 最高的参数值">最优参数</th>
                    <th className="text-right" title="最优参数在训练窗的夏普比率（样本内，偏乐观）">训练夏普 <span style={{ color: 'var(--text-muted)', fontWeight: 400 }}>样本内</span></th>
                    <th className="text-right" title="最优参数在训练窗的最大回撤">训练回撤</th>
                    <th className="text-right" title="该参数在测试窗（未见数据）的年化收益，正值才有实用价值">测试年化 <span style={{ color: '#00cec9', fontWeight: 400 }}>OOS★</span></th>
                    <th className="text-right" title="该参数在测试窗的夏普比率，越接近训练夏普说明越稳健">测试夏普 <span style={{ color: '#00cec9', fontWeight: 400 }}>OOS★</span></th>
                  </tr>
                </thead>
                <tbody>
                  {walkWindows.map((row, idx) => {
                    const testSharpe = row.test_metrics?.sharpe ?? 0;
                    const trainSharpe = row.train_metrics?.sharpe ?? 0;
                    const testAnnual = row.test_metrics?.annual_return ?? 0;
                    // 过拟合警示：测试夏普 < 0 或训练夏普比测试夏普高出超过 2
                    const isOverfit = testSharpe < 0 || (trainSharpe - testSharpe > 2);
                    const testColor = testSharpe >= 1 ? '#22c55e' : testSharpe >= 0 ? '#eab308' : '#ef4444';
                    return (
                      <tr key={idx} style={isOverfit ? { background: 'rgba(239,68,68,0.04)' } : undefined}>
                        <td style={{ color: 'var(--text-muted)', fontSize: '0.85rem' }}>
                          {String(row.train_start).slice(2)} ~ {String(row.train_end).slice(2)}
                        </td>
                        <td style={{ fontWeight: 600, fontSize: '0.85rem' }}>
                          {String(row.test_start).slice(2)} ~ {String(row.test_end).slice(2)}
                        </td>
                        <td>
                          <span style={{
                            background: 'rgba(108,92,231,0.15)', color: '#a29bfe',
                            borderRadius: 4, padding: '1px 6px', fontSize: '0.82rem', fontWeight: 600,
                          }}>
                            {findParamMeta(walkParam).label}={formatScanValue(walkParam, row.best_value)}
                          </span>
                        </td>
                        <td className="text-right" style={{ color: 'var(--text-muted)' }}>{num(row.train_metrics?.sharpe, 2)}</td>
                        <td className="text-right negative">{pct(row.train_metrics?.max_drawdown)}</td>
                        <td className="text-right" style={{ color: testAnnual >= 0 ? '#22c55e' : '#ef4444', fontWeight: 600 }}>
                          {pct(testAnnual)}
                        </td>
                        <td className="text-right" style={{ color: testColor, fontWeight: 600 }}>
                          {num(testSharpe, 2)}
                          {isOverfit && <span title="测试表现远低于训练，可能存在过拟合" style={{ marginLeft: 4, fontSize: '0.75rem' }}>⚠️</span>}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
            {/* 底部解读提示 */}
            <div style={{
              padding: '10px 14px', fontSize: '0.75rem', color: 'var(--text-muted)',
              borderTop: '1px solid var(--border-subtle)', lineHeight: 1.7,
            }}>
              <b>如何判断结果是否可信：</b>
              &nbsp;① 测试夏普 &gt;1 且多窗口稳定 → 策略具备泛化能力；
              &nbsp;② 训练夏普高但测试夏普低（⚠️标记）→ 该窗口过拟合，需关注；
              &nbsp;③ 参数命中分布集中在同一个值 → 参数选择稳定，可信度高
            </div>
          </div>
        </>
      )}

      {(mode === 'scan' || mode === 'scan2d') && scanRuns.length > 0 && (
        <div className="card" style={{ marginTop: 20 }}>
          <div className="card-header">
            <span className="card-title">📑 历史扫描</span>
            <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
              <span className="card-subtitle">{scanRuns.length} 条</span>
              <button className="btn btn-secondary btn-sm" style={{ color: 'var(--red)', fontSize: '0.75rem' }}
                onClick={async () => {
                  if (!confirm(`确认删除全部 ${scanRuns.length} 条扫描记录？`)) return;
                  for (const row of scanRuns) await api.deleteScan(row.id as number);
                  scanList.refetch();
                }}>🗑 清空全部</button>
            </div>
          </div>
          {scanRuns.map((row: Record<string, unknown>) => (
            <div key={row.id as number} className="signal-item" style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
              <div style={{ flex: 1, minWidth: 0, cursor: 'pointer' }} onClick={() => loadScan(row.id as number)}>
                <div style={{ fontWeight: 600, fontSize: '0.9rem', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                  {(row.name as string) || `#${row.id}`}
                </div>
                <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)', marginTop: 2 }}>
                  {String(row.scan_param)}
                </div>
              </div>
              <span className="muted" style={{ fontSize: '0.75rem', flexShrink: 0 }}>{String(row.created_at || '').slice(5, 16)}</span>
              <button className="btn btn-secondary btn-sm" title="删除" style={{ padding: '2px 8px', fontSize: '0.8rem', flexShrink: 0 }}
                onClick={async e => {
                  e.preventDefault();
                  e.stopPropagation();
                  if (!confirm(`删除扫描 "${row.name || row.id}"？`)) return;
                  await api.deleteScan(row.id as number);
                  scanList.refetch();
                }}>🗑</button>
            </div>
          ))}
        </div>
      )}

      {mode === 'single' && runs.length > 0 && (
        <div className="card" style={{ marginTop: 20 }}>
          <div className="card-header">
            <span className="card-title">📑 历史回测</span>
            <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
              <span className="card-subtitle">{runs.length} 条</span>
              <button className="btn btn-secondary btn-sm" style={{ color: 'var(--red)', fontSize: '0.75rem' }}
                onClick={async () => {
                  if (!confirm(`确认删除全部 ${runs.length} 条回测记录？`)) return;
                  for (const row of runs) await api.deleteBacktest(row.id as number);
                  list.refetch();
                }}>🗑 清空全部</button>
            </div>
          </div>
          {runs.map((row: Record<string, unknown>) => {
            const rm = (row.metrics || {}) as Record<string, number>;
            const rt = extractTradeStats(rm);
            return (
              <div key={row.id as number} className="signal-item" style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                <div style={{ flex: 1, minWidth: 0, cursor: 'pointer' }} onClick={() => loadRun(row.id as number)}>
                  <div style={{ fontWeight: 600, fontSize: '0.9rem', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                    {(row.name as string) || `#${row.id}`}
                  </div>
                  {rm.annual_return != null && (
                    <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)', marginTop: 2, display: 'flex', gap: 10, flexWrap: 'wrap' }}>
                      <span className={rm.annual_return >= 0 ? 'positive' : 'negative'}>CAGR {(rm.annual_return * 100).toFixed(1)}%</span>
                      <span className="negative">MDD {(rm.max_drawdown * 100).toFixed(1)}%</span>
                      <span>Sharpe {num(rm.sharpe, 2)}</span>
                      <span>交易胜率 {pct(rt.trade_win_rate)}</span>
                    </div>
                  )}
                </div>
                <span className="muted" style={{ fontSize: '0.75rem', flexShrink: 0 }}>{String(row.created_at || '').slice(5, 16)}</span>
                <button className="btn btn-secondary btn-sm" title="删除" style={{ padding: '2px 8px', fontSize: '0.8rem', flexShrink: 0 }}
                  onClick={async e => {
                    e.preventDefault();
                    e.stopPropagation();
                    if (!confirm(`删除回测 "${row.name || row.id}"？`)) return;
                    await api.deleteBacktest(row.id as number);
                    list.refetch();
                  }}>🗑</button>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
