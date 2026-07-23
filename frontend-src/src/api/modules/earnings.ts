/** 财报域：upcoming / refresh / AI 影响分析 */
import { get, post, mockOr } from '../client';
import { asRec, pickN, pickS, unwrap } from '../live';
import * as fx2 from '@/mocks/fixtures2';
import type { EarningsItem } from '../types';

export type EarningsImpactRelation = 'competitor' | 'supplier' | 'customer' | 'etf' | 'opposing';
export type EarningsImpactDirection = 'bullish' | 'bearish' | 'mixed';

export interface EarningsImpactItem {
  ticker: string;
  name: string;
  relation: EarningsImpactRelation;
  direction: EarningsImpactDirection;
  reason: string;
}

export interface EarningsImpactResult {
  outputLanguage: 'zh-CN';
  ticker: string;
  summary: string;
  expectation: string;
  impacted: EarningsImpactItem[];
}

export interface EarningsCalendarResult {
  items: EarningsItem[];
  dataLimited: boolean;
  sourceStatus: string;
  providers: string[];
  asOf: string | null;
  refreshStatus: string | null;
  refreshRetryAfterSeconds: number | null;
}

/**
 * 契约 {earnings:[{ticker,name,earnings_date,days_until,eps_estimate,eps_high,eps_low,
 * revenue_estimate,market_cap,sector,earnings_date_source,source_status,observed_at}]} → UI EarningsItem[]
 * 契约无 timing/eps_actual/revenue_actual：actual 一律 null（留空纪律）；
 * timing 缺失或值未知时保留 null，由界面明确显示“时间待定”；
 * snake_case 扩展字段原样保留供 exNum 读取。
 */
export function mapUpcoming(body: unknown): EarningsItem[] {
  return unwrap(body, 'earnings').map((r) => {
    const rawTiming = pickS(r, 'timing', 'earnings_time');
    const timing: EarningsItem['timing'] = rawTiming === 'bmo' || rawTiming === 'amc' ? rawTiming : null;
    return {
      ...r,
      ticker: pickS(r, 'ticker') ?? '',
      name: pickS(r, 'name') ?? '',
      date: pickS(r, 'date', 'earnings_date') ?? '',
      timing,
      epsEstimate: pickN(r, 'epsEstimate', 'eps_estimate'),
      epsActual: pickN(r, 'epsActual', 'eps_actual'),
      revEstimate: pickN(r, 'revEstimate', 'revenue_estimate'),
      revActual: pickN(r, 'revActual', 'revenue_actual'),
    };
  });
}

export function mapUpcomingPayload(body: unknown): EarningsCalendarResult {
  const row = asRec(body);
  const providers = Array.isArray(row.providers)
    ? row.providers.filter((value): value is string => typeof value === 'string' && value.length > 0)
    : [];
  return {
    items: mapUpcoming(body),
    dataLimited: row.data_limited === true,
    sourceStatus: pickS(row, 'source_status') ?? 'unknown',
    providers,
    asOf: pickS(row, 'as_of'),
    refreshStatus: pickS(row, 'refresh_status'),
    refreshRetryAfterSeconds: pickN(row, 'refresh_retry_after_seconds'),
  };
}

const RELATIONS = new Set<EarningsImpactRelation>(['competitor', 'supplier', 'customer', 'etf', 'opposing']);
const DIRECTIONS = new Set<EarningsImpactDirection>(['bullish', 'bearish', 'mixed']);

/**
 * 已完成财报任务的真实契约：
 * {output_language,ticker,summary,expectation,impacted:[{ticker,name,relation,direction,reason}]}
 * 在线模式不为缺失字段补默认值，避免把不完整响应包装成已完成分析。
 */
export function normalizeLiveEarningsImpact(body: unknown): EarningsImpactResult {
  const row = asRec(body);
  const outputLanguage = pickS(row, 'outputLanguage', 'output_language');
  const ticker = pickS(row, 'ticker');
  const summary = pickS(row, 'summary');
  const expectation = pickS(row, 'expectation');
  const rawImpacted = Array.isArray(row.impacted) ? row.impacted : null;

  if (outputLanguage !== 'zh-CN' || !ticker || !summary || !expectation || !rawImpacted) {
    throw new Error('财报影响分析返回字段不完整');
  }

  const impacted = rawImpacted.map((value) => {
    const item = asRec(value);
    const itemTicker = pickS(item, 'ticker');
    const name = pickS(item, 'name');
    const relation = pickS(item, 'relation');
    const direction = pickS(item, 'direction');
    const reason = pickS(item, 'reason');
    if (
      !itemTicker
      || !name
      || !relation
      || !RELATIONS.has(relation as EarningsImpactRelation)
      || !direction
      || !DIRECTIONS.has(direction as EarningsImpactDirection)
      || !reason
    ) {
      throw new Error('财报影响分析的关联标的字段不完整');
    }
    return {
      ticker: itemTicker.toUpperCase(),
      name,
      relation: relation as EarningsImpactRelation,
      direction: direction as EarningsImpactDirection,
      reason,
    };
  });

  return {
    outputLanguage: 'zh-CN',
    ticker: ticker.toUpperCase(),
    summary,
    expectation,
    impacted,
  };
}

/** 旧版演示夹具仅用于本地 mock；在线响应始终走严格的真实契约归一化。 */
function normalizeMockEarningsImpact(body: unknown): EarningsImpactResult {
  try {
    return normalizeLiveEarningsImpact(body);
  } catch {
    const row = asRec(body);
    const ticker = pickS(row, 'ticker') ?? '';
    const summary = pickS(row, 'summary') ?? '暂无分析摘要';
    const related = Array.isArray(row.related) ? row.related : [];
    return {
      outputLanguage: 'zh-CN',
      ticker: ticker.toUpperCase(),
      summary,
      expectation: summary,
      impacted: related.map((value) => {
        const item = asRec(value);
        const change = pickN(item, 'changePct', 'change_pct');
        return {
          ticker: (pickS(item, 'ticker') ?? '').toUpperCase(),
          name: pickS(item, 'name') ?? pickS(item, 'ticker') ?? '',
          relation: 'opposing',
          direction: change == null ? 'mixed' : change > 0 ? 'bullish' : change < 0 ? 'bearish' : 'mixed',
          reason: pickS(item, 'reason', 'relation') ?? '本地演示关联项',
        };
      }),
    };
  }
}

export const earningsApi = {
  upcoming: (): Promise<EarningsCalendarResult> =>
    mockOr(
      () => ({
        items: fx2.getEarningsUpcoming(),
        dataLimited: false,
        sourceStatus: 'active',
        providers: ['本地演示数据'],
        asOf: new Date().toISOString(),
        refreshStatus: null,
        refreshRetryAfterSeconds: null,
      }),
      () => get('/earnings/upcoming').then(mapUpcomingPayload),
    ),
  refresh: (): Promise<EarningsCalendarResult> =>
    // 契约：owner+SO → {earnings:[...], refresh_status∈refreshed|cooldown|failed_stale}
    mockOr(
      () => ({
        items: fx2.refreshEarningsUpcoming(),
        dataLimited: false,
        sourceStatus: 'active',
        providers: ['本地演示数据'],
        asOf: new Date().toISOString(),
        refreshStatus: 'refreshed',
        refreshRetryAfterSeconds: 60,
      }),
      () => post('/earnings/upcoming/refresh').then(mapUpcomingPayload),
    ),
  impact: (ticker: string): Promise<EarningsImpactResult> =>
    // 409 analysis_required 由 client 统一透传（code + bizCode），UI 状态机消费
    mockOr(
      () => normalizeMockEarningsImpact(fx2.getEarningsImpact(ticker)),
      () => get(`/ai/earnings-impact/${encodeURIComponent(ticker)}`).then(normalizeLiveEarningsImpact),
    ),
};
