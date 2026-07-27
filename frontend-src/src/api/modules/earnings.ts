/** 财报域：upcoming / refresh / AI 影响分析 */
import { get, post, mockOr, toQuery } from '../client';
import { registryGet, restorePersistedQuery } from '../queryRegistry';
import { asRec, pickN, pickS, pickLabel, unwrap } from '../live';
import * as fx2 from '@/mocks/fixtures2';
import type { EarningsItem } from '../types';
import { t } from '../../i18n/core.ts';

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
  /** 后端缺少这些元数据时保持 undefined，兼容尚未升级的缓存结果。 */
  analysisStage?: string;
  locked?: boolean;
  final?: boolean;
  finalizationInProgress?: boolean;
  reportId?: string;
  reportDate?: string;
}

export interface EarningsReportIdentity {
  year?: number | null;
  quarter?: number | null;
}

export interface EarningsReportAnalysis {
  status: string;
  errorCode: string | null;
  retryAfterSeconds: number | null;
  analysisStage?: string;
  locked: boolean;
  final: boolean;
  finalizationInProgress: boolean;
  reportId?: string;
  reportDate?: string;
  result: EarningsImpactResult | null;
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
  const rows: EarningsItem[] = [];
  unwrap(body, 'earnings').forEach((r) => {
    // 缺股票代码或财报日期的行无法定位到任何一场财报（审计 P2-31）：
    // 补成空串会让空日期继续进入日期计算、分组与自动选中逻辑。
    const rowTicker = pickS(r, 'ticker');
    const rowDate = pickS(r, 'date', 'earnings_date');
    if (!rowTicker || !rowDate) return;
    const rawTiming = pickS(r, 'timing', 'earnings_time');
    const timing: EarningsItem['timing'] = rawTiming === 'bmo' || rawTiming === 'amc' ? rawTiming : null;
    const rawMarketCap = pickN(r, 'marketCap', 'market_cap');
    const marketCap = rawMarketCap != null && rawMarketCap > 0 ? rawMarketCap : null;
    rows.push({
      ...(r as Record<string, unknown>),
      // 同时覆盖两种命名，避免旧响应里的 0 被扩展字段读取逻辑重新捡回。
      market_cap: marketCap,
      marketCap,
      ticker: rowTicker,
      name: pickLabel(r, 'name') ?? rowTicker,
      date: rowDate,
      timing,
      epsEstimate: pickN(r, 'epsEstimate', 'eps_estimate'),
      epsActual: pickN(r, 'epsActual', 'eps_actual'),
      revEstimate: pickN(r, 'revEstimate', 'revenue_estimate'),
      revActual: pickN(r, 'revActual', 'revenue_actual'),
    } as unknown as EarningsItem);
  });
  return rows;
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

function pickOptionalBool(
  row: Record<string, unknown>,
  ...keys: string[]
): boolean | null {
  for (const key of keys) {
    if (typeof row[key] === 'boolean') return row[key];
  }
  return null;
}

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
  const analysisStage = pickS(row, 'analysisStage', 'analysis_stage', '_analysis_stage');
  const locked = pickOptionalBool(row, 'locked', '_locked');
  const final = pickOptionalBool(row, 'final', '_final');
  const finalizationInProgress = pickOptionalBool(
    row,
    'finalizationInProgress',
    'finalization_in_progress',
    '_finalization_in_progress',
  );
  const reportId = pickS(row, 'reportId', 'report_id', '_report_id');
  const reportDate = pickS(row, 'reportDate', 'report_date', '_report_date');

  if (outputLanguage !== 'zh-CN' || !ticker || !summary || !expectation || !rawImpacted) {
    throw new Error(t('财报影响分析返回字段不完整'));
  }

  const impacted = rawImpacted.map((value) => {
    const item = asRec(value);
    const itemTicker = pickS(item, 'ticker');
    const name = pickLabel(item, 'name');
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
      throw new Error(t('财报影响分析的关联标的字段不完整'));
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
    ...(analysisStage ? { analysisStage } : {}),
    ...(locked !== null ? { locked } : {}),
    ...(final !== null ? { final } : {}),
    ...(finalizationInProgress !== null ? { finalizationInProgress } : {}),
    ...(reportId ? { reportId } : {}),
    ...(reportDate ? { reportDate } : {}),
  };
}

/** 报告级公开接口只返回状态、锁元数据与可选结果，不暴露任务编号、费用或取消能力。 */
export function normalizeEarningsReportAnalysis(body: unknown): EarningsReportAnalysis {
  const row = asRec(body);
  const analysisStage = pickS(row, 'analysisStage', 'analysis_stage', '_analysis_stage');
  const locked = pickOptionalBool(row, 'locked', '_locked') === true;
  const final = pickOptionalBool(row, 'final', '_final') === true;
  const finalizationInProgress = pickOptionalBool(
    row,
    'finalizationInProgress',
    'finalization_in_progress',
    '_finalization_in_progress',
  ) === true;
  const reportId = pickS(row, 'reportId', 'report_id', '_report_id');
  const reportDate = pickS(row, 'reportDate', 'report_date', '_report_date');
  const rawResult = asRec(row.result);
  let result: EarningsImpactResult | null = null;
  if (Object.keys(rawResult).length > 0) {
    const normalized = normalizeLiveEarningsImpact(rawResult);
    result = {
      ...normalized,
      ...(analysisStage ? { analysisStage } : {}),
      ...(locked ? { locked: true } : {}),
      ...(final ? { final: true } : {}),
      ...(finalizationInProgress ? { finalizationInProgress: true } : {}),
      ...(reportId ? { reportId } : {}),
      ...(reportDate ? { reportDate } : {}),
    };
  }
  return {
    status: pickS(row, 'status') ?? (result ? 'completed' : 'not_requested'),
    errorCode: pickS(row, 'errorCode', 'error_code'),
    retryAfterSeconds: pickN(row, 'retryAfterSeconds', 'retry_after_seconds'),
    ...(analysisStage ? { analysisStage } : {}),
    locked,
    final,
    finalizationInProgress,
    ...(reportId ? { reportId } : {}),
    ...(reportDate ? { reportDate } : {}),
    result,
  };
}

function reportAnalysisPath(
  ticker: string,
  reportDate: string,
  identity: EarningsReportIdentity,
): string {
  const query = toQuery({
    year: identity.year,
    quarter: identity.quarter,
  });
  const suffix = query ? `?${query}` : '';
  return `/ai/earnings-impact/${encodeURIComponent(ticker)}/reports/${encodeURIComponent(reportDate)}${suffix}`;
}

/** 旧版演示夹具仅用于本地 mock；在线响应始终走严格的真实契约归一化。 */
function normalizeMockEarningsImpact(body: unknown): EarningsImpactResult {
  try {
    return normalizeLiveEarningsImpact(body);
  } catch {
    const row = asRec(body);
    const ticker = pickS(row, 'ticker') ?? '';
    const summary = pickS(row, 'summary') ?? t('暂无分析摘要');
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
          name: pickLabel(item, 'name') ?? pickS(item, 'ticker') ?? '',
          relation: 'opposing',
          direction: change == null ? 'mixed' : change > 0 ? 'bullish' : change < 0 ? 'bearish' : 'mixed',
          reason: pickS(item, 'reason', 'relation') ?? t('本地演示关联项'),
        };
      }),
    };
  }
}

/**
 * 冷启动恢复:上一份持久化的财报快照(不发网络)。
 * 返回值与 upcoming() 同形状;数据截至时间以 payload 里的 asOf 为准。
 */
export async function restoreUpcomingFromCache(): Promise<EarningsCalendarResult | null> {
  const raw = await restorePersistedQuery('/earnings/upcoming');
  return raw === null ? null : mapUpcomingPayload(raw);
}

export const earningsApi = {
  upcoming: (): Promise<EarningsCalendarResult> =>
    mockOr(
      () => ({
        items: fx2.getEarningsUpcoming(),
        dataLimited: false,
        sourceStatus: 'active',
        providers: [t('本地演示数据')],
        asOf: new Date().toISOString(),
        refreshStatus: null,
        refreshRetryAfterSeconds: null,
      }),
      () => registryGet('/earnings/upcoming').then(mapUpcomingPayload),
    ),
  refresh: (): Promise<EarningsCalendarResult> =>
    // 契约：owner+SO → {earnings:[...], refresh_status∈refreshed|cooldown|failed_stale}
    mockOr(
      () => ({
        items: fx2.refreshEarningsUpcoming(),
        dataLimited: false,
        sourceStatus: 'active',
        providers: [t('本地演示数据')],
        asOf: new Date().toISOString(),
        refreshStatus: 'refreshed',
        refreshRetryAfterSeconds: 60,
      }),
      () => post('/earnings/upcoming/refresh').then(mapUpcomingPayload),
    ),
  /** 旧版按代码读取仅供仍未迁移的所有者视图；财报日历使用下方精确报告级接口。 */
  impact: (ticker: string): Promise<EarningsImpactResult> =>
    // 409 analysis_required 由 client 统一透传（code + bizCode），UI 状态机消费
    mockOr(
      () => normalizeMockEarningsImpact(fx2.getEarningsImpact(ticker)),
      () => get(`/ai/earnings-impact/${encodeURIComponent(ticker)}`).then(normalizeLiveEarningsImpact),
    ),
  reportAnalysis: (
    ticker: string,
    reportDate: string,
    identity: EarningsReportIdentity = {},
  ): Promise<EarningsReportAnalysis> =>
    mockOr(
      () => ({
        status: 'completed',
        errorCode: null,
        retryAfterSeconds: null,
        locked: false,
        final: false,
        finalizationInProgress: false,
        reportDate,
        result: normalizeMockEarningsImpact(fx2.getEarningsImpact(ticker)),
      }),
      () => get(reportAnalysisPath(ticker, reportDate, identity)).then(normalizeEarningsReportAnalysis),
    ),
  requestReportAnalysis: (
    ticker: string,
    reportDate: string,
    identity: EarningsReportIdentity = {},
  ): Promise<EarningsReportAnalysis> =>
    mockOr(
      () => ({
        status: 'queued',
        errorCode: null,
        retryAfterSeconds: 2,
        locked: false,
        final: false,
        finalizationInProgress: false,
        reportDate,
        result: null,
      }),
      () => post(reportAnalysisPath(ticker, reportDate, identity), { confirm: true })
        .then(normalizeEarningsReportAnalysis),
    ),
};
