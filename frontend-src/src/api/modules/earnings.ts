/** 财报域：upcoming / refresh / AI 影响分析 */
import { get, post, mockOr } from '../client';
import { pickN, pickS, unwrap } from '../live';
import * as fx2 from '@/mocks/fixtures2';
import type { EarningsImpact, EarningsItem } from '../types';

/**
 * 契约 {earnings:[{ticker,name,earnings_date,days_until,eps_estimate,eps_high,eps_low,
 * revenue_estimate,market_cap,sector,earnings_date_source,source_status,observed_at}]} → UI EarningsItem[]
 * 契约无 timing/eps_actual/revenue_actual：actual 一律 null（留空纪律）；
 * timing 缺失回退 'bmo'（残余风险见 AUDIT-live.md）；snake_case 扩展字段原样保留供 exNum 读取。
 */
function mapUpcoming(body: unknown): EarningsItem[] {
  return unwrap(body, 'earnings').map((r) => ({
    ...r,
    ticker: pickS(r, 'ticker') ?? '',
    name: pickS(r, 'name') ?? '',
    date: pickS(r, 'date', 'earnings_date') ?? '',
    timing: (pickS(r, 'timing', 'earnings_time') as EarningsItem['timing']) ?? 'bmo',
    epsEstimate: pickN(r, 'epsEstimate', 'eps_estimate'),
    epsActual: pickN(r, 'epsActual', 'eps_actual'),
    revEstimate: pickN(r, 'revEstimate', 'revenue_estimate'),
    revActual: pickN(r, 'revActual', 'revenue_actual'),
  }));
}

export const earningsApi = {
  upcoming: (): Promise<EarningsItem[]> =>
    mockOr(() => fx2.getEarningsUpcoming(), () => get('/earnings/upcoming').then(mapUpcoming)),
  refresh: (): Promise<EarningsItem[]> =>
    // 契约：owner+SO → {earnings:[...], refresh_status∈refreshed|cooldown|failed_stale}
    mockOr(() => fx2.refreshEarningsUpcoming(), () => post('/earnings/upcoming/refresh').then(mapUpcoming)),
  impact: (ticker: string): Promise<EarningsImpact> =>
    // 409 analysis_required 由 client 统一透传（code + bizCode），UI 状态机消费
    mockOr(() => fx2.getEarningsImpact(ticker), () => get(`/ai/earnings-impact/${encodeURIComponent(ticker)}`)),
};
