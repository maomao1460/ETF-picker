// 作者：相空
type MarketPayload = Record<string, unknown> | null | undefined;

type SentimentLevel = 'strong' | 'neutral' | 'weak';

function asObject(value: unknown): Record<string, unknown> | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    return null;
  }
  return value as Record<string, unknown>;
}

function asNumber(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

function asBoolean(value: unknown): boolean | null {
  return typeof value === 'boolean' ? value : null;
}

function normalizeSentimentLevel(value: unknown): SentimentLevel | null {
  if (value === 'strong' || value === '强势') return 'strong';
  if (value === 'neutral' || value === '中性') return 'neutral';
  if (value === 'weak' || value === '弱势') return 'weak';
  return null;
}

function getSentimentPct(market: MarketPayload): number | null {
  if (!market) return null;

  const pct = asNumber(market.sentiment_pct);
  if (pct != null) return pct;

  const breadth = getBreadthText(market);
  if (breadth) {
    const [positiveText, totalText] = breadth.split('/');
    const positive = Number(positiveText);
    const total = Number(totalText);
    if (Number.isFinite(positive) && Number.isFinite(total) && total > 0) {
      return positive / total * 100;
    }
  }

  const positive = asNumber(market.positive_sectors);
  const total = asNumber(market.total_sectors);
  if (positive != null && total != null && total > 0) {
    return positive / total * 100;
  }

  return null;
}

export function getBreadthText(market: MarketPayload): string | null {
  if (!market) return null;

  if (typeof market.breadth === 'string' && market.breadth) {
    return market.breadth;
  }

  const positive = asNumber(market.positive_sectors);
  const total = asNumber(market.total_sectors);
  if (positive != null && total != null && total > 0) {
    return `${positive}/${total}`;
  }

  return null;
}

export function getSentimentMeta(market: MarketPayload): {
  level: SentimentLevel;
  label: string;
  badgeClass: string;
  text: string;
} | null {
  const pct = getSentimentPct(market);
  let level = normalizeSentimentLevel(market?.sentiment_level);

  if (!level && pct != null) {
    if (pct >= 70) level = 'strong';
    else if (pct >= 40) level = 'neutral';
    else level = 'weak';
  }

  if (!level) return null;

  const meta = {
    strong: { label: '强势', badgeClass: 'badge-green' },
    neutral: { label: '中性', badgeClass: 'badge-yellow' },
    weak: { label: '弱势', badgeClass: 'badge-red' },
  }[level];

  return {
    level,
    ...meta,
    text: pct != null ? `${meta.label} (${pct.toFixed(1)}%)` : meta.label,
  };
}

export function getMa200Meta(
  market: MarketPayload,
  enabled?: boolean | null,
): {
  riskOn: boolean | null;
  badgeClass: string;
  label: string;
} | null {
  const status = asObject(market?.ma200_status);
  let riskOn = asBoolean(status?.risk_on);

  if (riskOn == null) {
    if (market?.ma200_status === 'risk_on') riskOn = true;
    if (market?.ma200_status === 'risk_off') riskOn = false;
  }

  if (riskOn == null) {
    const riskOff = asBoolean(market?.risk_off);
    if (riskOff != null) {
      riskOn = !riskOff;
    }
  }

  if (enabled === false) {
    if (riskOn == null) {
      return { riskOn: null, badgeClass: 'badge-yellow', label: 'MA200风控: 已关闭' };
    }
    return {
      riskOn,
      badgeClass: 'badge-yellow',
      label: `MA200风控: 已关闭 (${riskOn ? '基准在MA200上方' : '基准在MA200下方'})`,
    };
  }

  if (riskOn == null) return null;

  return {
    riskOn,
    badgeClass: riskOn ? 'badge-green' : 'badge-red',
    label: `MA200风控: ${riskOn ? '✅允许' : '🚫禁止'}`,
  };
}

export function normalizeSignalAction(action: unknown): 'buy' | 'sell' | null {
  if (action === 'buy' || action === '买入') return 'buy';
  if (action === 'sell' || action === '卖出') return 'sell';
  return null;
}
