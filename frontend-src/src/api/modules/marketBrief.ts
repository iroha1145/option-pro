/**
 * 首页「市场综合研判」域（market_brief）
 * GET  /api/market-brief/latest  匿名可读：status="ok" 带 brief；status="missing" 时 brief 为 null，
 *                                仍给 latest_attempt 与 next_slot（样例 tests/fixtures/market_brief_sample.json）
 * POST /api/market-brief/runs    Owner：body {slot?: pre_open|post_close}。新排队 202；已在跑、同一分钟
 *                                重复提交、冷却中为 200，靠 reason 区分（backend/app/api/market_brief.py）；
 *                                关闭或缺密钥 409，当日次数用完 429，后台不可用 503
 *
 * 归一原则（同 api/live.ts「不造假」）：
 * - 顶层字段映射成 camelCase；result 内部保留后端 snake_case 字段名，便于和
 *   backend/app/services/market_brief/schema.py 逐字段对照；
 * - 枚举走白名单，认不出的值回落到该枚举自己的「不明」成员；证据充分度认不出时为 null
 *   （界面显「—」），不回落成「低」——那等于替模型下了一个判断；
 * - 外部来源只保留 http(s) 链接，链接来自联网检索结果，不能原样信任。
 */
import { isMock, mockOr, postCreate } from '../client';
import { registryGet, restorePersistedQuery } from '../queryRegistry';
import { asRec, pickN, pickS, type Rec } from '../live';
import * as fx from '@/mocks/marketBrief';

export const MARKET_BRIEF_LATEST_PATH = '/market-brief/latest';

/* ---------------------------------- 类型 ---------------------------------- */

export type BriefSlot = 'pre_open' | 'post_close';
export type BriefTrigger = 'scheduled' | 'manual';
export type BriefRegime =
  | 'broad_advance'
  | 'narrow_leadership'
  | 'rotation'
  | 'risk_off'
  | 'mixed'
  | 'uncertain';
export type BriefSufficiency = 'low' | 'medium' | 'high';
export type BriefConsistency = 'confirms' | 'diverges' | 'mixed' | 'unknown';
export type BriefMacroVerdict = 'supports' | 'contradicts' | 'mixed' | 'unknown';
export type BriefSectorChange = 'substantive' | 'noise' | 'unknown';
export type BriefPricedIn = 'yes' | 'partly' | 'no' | 'unclear';

const SLOTS: readonly BriefSlot[] = ['pre_open', 'post_close'];
const TRIGGERS: readonly BriefTrigger[] = ['scheduled', 'manual'];
const REGIMES: readonly BriefRegime[] = [
  'broad_advance',
  'narrow_leadership',
  'rotation',
  'risk_off',
  'mixed',
  'uncertain',
];
const SUFFICIENCIES: readonly BriefSufficiency[] = ['low', 'medium', 'high'];
const CONSISTENCIES: readonly BriefConsistency[] = ['confirms', 'diverges', 'mixed', 'unknown'];
const MACRO_VERDICTS: readonly BriefMacroVerdict[] = ['supports', 'contradicts', 'mixed', 'unknown'];
const SECTOR_CHANGES: readonly BriefSectorChange[] = ['substantive', 'noise', 'unknown'];
const PRICED_IN: readonly BriefPricedIn[] = ['yes', 'partly', 'no', 'unclear'];

/** 一段研判：总结、要点、引用的证据 id。 */
export interface BriefSection {
  summary: string | null;
  points: string[];
  evidence_ids: string[];
}

export interface BriefInternals extends BriefSection {
  breadth_vs_index: BriefConsistency;
}

export interface BriefMacroCheck extends BriefSection {
  verdict: BriefMacroVerdict;
}

export interface BriefSectorRead {
  name: string;
  change: BriefSectorChange;
  note: string | null;
  evidence_ids: string[];
}

export interface BriefNewsRead {
  evidence_id: string | null;
  title_zh: string;
  what_is_new: string | null;
  priced_in: BriefPricedIn;
  tickers: string[];
}

export interface BriefWatchItem {
  what: string;
  why: string | null;
  revise_if: string | null;
}

/** 模型输出（字段名与 schema.py 的 MarketBriefResult 相同）。 */
export interface MarketBriefResult {
  headline: string | null;
  regime: BriefRegime;
  evidence_sufficiency: BriefSufficiency | null;
  internals: BriefInternals;
  macro_check: BriefMacroCheck;
  sectors: BriefSectorRead[];
  key_news: BriefNewsRead[];
  watch_items: BriefWatchItem[];
  invalidators: string[];
  prior_review: string | null;
}

export interface MarketBriefModel {
  id: string | null;
  label: string | null;
  effort: string | null;
}

export interface BriefMissingBlock {
  block: string;
  reason: string | null;
}

/** 覆盖范围由程序写入，不是模型生成。 */
export interface MarketBriefCoverage {
  universeSize: number | null;
  scoredCount: number | null;
  quotesValid: number | null;
  breadthBasis: string | null;
  /** 各数据源截止时间：日期（YYYY-MM-DD）或 ISO 时刻，按源原样保留。 */
  dataThrough: Record<string, string>;
  missingBlocks: BriefMissingBlock[];
}

export interface BriefSource {
  url: string;
  title: string | null;
}

export interface MarketBrief {
  runId: string | null;
  tradingDate: string | null;
  slot: BriefSlot | null;
  trigger: BriefTrigger | null;
  generatedAt: string | null;
  model: MarketBriefModel;
  coverage: MarketBriefCoverage;
  result: MarketBriefResult;
  externalSources: BriefSource[];
  validationWarnings: string[];
  webSearchCount: number | null;
}

/**
 * 比当前研判更新的那次失败运行。公开投影只有 error_code / at / slot / trading_date
 * （store.py 的 latest_public）；run_id、trigger、status 按候选键宽容读取，没有时为 null。
 */
export interface MarketBriefAttempt {
  runId: string | null;
  tradingDate: string | null;
  slot: BriefSlot | null;
  trigger: BriefTrigger | null;
  status: string | null;
  at: string | null;
  errorCode: string | null;
}

export interface MarketBriefNextSlot {
  slot: BriefSlot;
  at: string;
}

export interface MarketBriefLatest {
  /** 'ok' | 'missing'；后端新增的状态原样保留，界面只按 brief 是否存在分支。 */
  status: string;
  schemaVersion: string | null;
  brief: MarketBrief | null;
  latestAttempt: MarketBriefAttempt | null;
  nextSlot: MarketBriefNextSlot | null;
  snapshotSavedAt: string | null;
}

/** POST /runs 的受理结果：reason 为空是新排队；already_running / idempotent / cooldown 见接口说明。 */
export interface MarketBriefTriggerResult {
  requestId: string | null;
  status: string | null;
  reason: string | null;
  cooldownUntil: string | null;
  errorCode: string | null;
}

/* --------------------------------- 归一化 --------------------------------- */

function oneOf<T extends string>(allowed: readonly T[], value: string | null): T | null {
  return allowed.includes(value as T) ? (value as T) : null;
}

function texts(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value
    .filter((item): item is string => typeof item === 'string')
    .map((item) => item.trim())
    .filter((item) => item.length > 0);
}

function text(r: Rec, key: string): string | null {
  const value = pickS(r, key);
  const trimmed = value?.trim() ?? '';
  return trimmed ? trimmed : null;
}

function rows(value: unknown): Rec[] {
  return Array.isArray(value) ? value.map(asRec) : [];
}

const TICKER = /^[A-Z0-9.^-]{1,12}$/;

function tickers(value: unknown): string[] {
  return texts(value)
    .map((item) => item.toUpperCase())
    .filter((item) => TICKER.test(item));
}

function mapSection(value: unknown): BriefSection {
  const r = asRec(value);
  return {
    summary: text(r, 'summary'),
    points: texts(r.points),
    evidence_ids: texts(r.evidence_ids),
  };
}

export function mapResult(value: unknown): MarketBriefResult {
  const r = asRec(value);
  const internals = asRec(r.internals);
  const macro = asRec(r.macro_check);
  return {
    headline: text(r, 'headline'),
    regime: oneOf(REGIMES, pickS(r, 'regime')) ?? 'uncertain',
    evidence_sufficiency: oneOf(SUFFICIENCIES, pickS(r, 'evidence_sufficiency')),
    internals: {
      ...mapSection(internals),
      breadth_vs_index: oneOf(CONSISTENCIES, pickS(internals, 'breadth_vs_index')) ?? 'unknown',
    },
    macro_check: {
      ...mapSection(macro),
      verdict: oneOf(MACRO_VERDICTS, pickS(macro, 'verdict')) ?? 'unknown',
    },
    sectors: rows(r.sectors).flatMap((row) => {
      const name = text(row, 'name');
      if (!name) return [];
      return [{
        name,
        change: oneOf(SECTOR_CHANGES, pickS(row, 'change')) ?? 'unknown',
        note: text(row, 'note'),
        evidence_ids: texts(row.evidence_ids),
      }];
    }),
    key_news: rows(r.key_news).flatMap((row) => {
      const title = text(row, 'title_zh');
      if (!title) return [];
      return [{
        evidence_id: text(row, 'evidence_id'),
        title_zh: title,
        what_is_new: text(row, 'what_is_new'),
        priced_in: oneOf(PRICED_IN, pickS(row, 'priced_in')) ?? 'unclear',
        tickers: tickers(row.tickers),
      }];
    }),
    watch_items: rows(r.watch_items).flatMap((row) => {
      const what = text(row, 'what');
      if (!what) return [];
      return [{ what, why: text(row, 'why'), revise_if: text(row, 'revise_if') }];
    }),
    invalidators: texts(r.invalidators),
    prior_review: text(r, 'prior_review'),
  };
}

function mapCoverage(value: unknown): MarketBriefCoverage {
  const r = asRec(value);
  const through = asRec(r.data_through);
  const dataThrough: Record<string, string> = {};
  for (const [block, at] of Object.entries(through)) {
    if (typeof at === 'string' && at.trim()) dataThrough[block] = at.trim();
  }
  const missingBlocks = Array.isArray(r.missing_blocks)
    ? r.missing_blocks.flatMap((item): BriefMissingBlock[] => {
        if (typeof item === 'string' && item.trim()) return [{ block: item.trim(), reason: null }];
        const row = asRec(item);
        const block = text(row, 'block');
        return block ? [{ block, reason: text(row, 'reason') }] : [];
      })
    : [];
  return {
    universeSize: pickN(r, 'universe_size'),
    scoredCount: pickN(r, 'scored_count'),
    quotesValid: pickN(r, 'quotes_valid'),
    breadthBasis: text(r, 'breadth_basis'),
    dataThrough,
    missingBlocks,
  };
}

function safeUrl(value: string | null): string | null {
  if (!value) return null;
  try {
    const url = new URL(value);
    return url.protocol === 'https:' || url.protocol === 'http:' ? url.href : null;
  } catch {
    return null;
  }
}

function mapSources(value: unknown): BriefSource[] {
  const seen = new Set<string>();
  return rows(value).flatMap((row) => {
    const url = safeUrl(text(row, 'url'));
    if (!url || seen.has(url)) return [];
    seen.add(url);
    return [{ url, title: text(row, 'title') }];
  });
}

function mapBrief(value: unknown): MarketBrief | null {
  if (value === null || typeof value !== 'object' || Array.isArray(value)) return null;
  const r = value as Rec;
  if (r.result === null || typeof r.result !== 'object') return null;
  const model = asRec(r.model);
  return {
    runId: text(r, 'run_id'),
    tradingDate: text(r, 'trading_date'),
    slot: oneOf(SLOTS, pickS(r, 'slot')),
    trigger: oneOf(TRIGGERS, pickS(r, 'trigger')),
    generatedAt: text(r, 'generated_at'),
    model: {
      id: text(model, 'id'),
      label: text(model, 'label'),
      effort: text(model, 'effort'),
    },
    coverage: mapCoverage(r.coverage),
    result: mapResult(r.result),
    externalSources: mapSources(r.external_sources),
    validationWarnings: texts(r.validation_warnings),
    webSearchCount: pickN(r, 'web_search_count'),
  };
}

function mapAttempt(value: unknown): MarketBriefAttempt | null {
  if (value === null || typeof value !== 'object' || Array.isArray(value)) return null;
  const r = value as Rec;
  return {
    runId: text(r, 'run_id'),
    tradingDate: text(r, 'trading_date'),
    slot: oneOf(SLOTS, pickS(r, 'slot')),
    trigger: oneOf(TRIGGERS, pickS(r, 'trigger')),
    status: text(r, 'status'),
    at: pickS(r, 'at', 'completed_at', 'finished_at', 'started_at'),
    errorCode: text(r, 'error_code'),
  };
}

function mapNextSlot(value: unknown): MarketBriefNextSlot | null {
  const r = asRec(value);
  const slot = oneOf(SLOTS, pickS(r, 'slot'));
  const at = text(r, 'at');
  return slot && at ? { slot, at } : null;
}

export function mapLatest(body: unknown): MarketBriefLatest {
  const r = asRec(body);
  const brief = mapBrief(r.brief);
  return {
    status: text(r, 'status') ?? (brief ? 'ok' : 'missing'),
    schemaVersion: text(r, 'schema_version'),
    brief,
    latestAttempt: mapAttempt(r.latest_attempt),
    nextSlot: mapNextSlot(r.next_slot),
    snapshotSavedAt: text(r, 'snapshot_saved_at'),
  };
}

export function mapTrigger(body: unknown): MarketBriefTriggerResult {
  const r = asRec(body);
  return {
    requestId: text(r, 'request_id'),
    status: text(r, 'status'),
    reason: text(r, 'reason'),
    cooldownUntil: text(r, 'cooldown_until'),
    errorCode: text(r, 'error_code'),
  };
}

/* ---------------------------------- API ---------------------------------- */

export const marketBriefApi = {
  /** 还没有任何研判时后端仍回 200（status="missing"），不会走错误分支。 */
  latest: (): Promise<MarketBriefLatest> =>
    mockOr(
      () => mapLatest(fx.getMarketBriefLatest()),
      () => registryGet<unknown>(MARKET_BRIEF_LATEST_PATH).then(mapLatest),
    ),
  /** 冷启动先显示上一份持久化的研判（不发网络）；没有记录时为 null。 */
  restore: (): Promise<MarketBriefLatest | null> =>
    isMock
      ? Promise.resolve(null)
      : restorePersistedQuery<unknown>(MARKET_BRIEF_LATEST_PATH).then((raw) =>
          raw === null ? null : mapLatest(raw),
        ),
  /** Owner 手动生成；不传 slot 时由 Worker 按美东钟点决定补哪一份。202 可以不带正文。 */
  trigger: (slot?: BriefSlot): Promise<MarketBriefTriggerResult> =>
    mockOr(
      () => mapTrigger(fx.triggerMarketBrief()),
      () => postCreate<unknown>('/market-brief/runs', slot ? { slot } : {}).then(({ data }) => mapTrigger(data)),
    ),
};
