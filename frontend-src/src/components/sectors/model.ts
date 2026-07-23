/**
 * 板块透视页 · 视图模型与归一化（src/components/sectors/model.ts）
 * - 同时兼容 mock 扩展形状（SectorSnapshot / SectorIvRow）与 live 契约形状
 *   （{id,name,tickers[]} 与 {ticker,name,price,atm_iv_percent,sector_iv_rank,_stale,as_of}）
 * - 数据诚信：接口未覆盖的字段归一化为 null，UI 如实渲染「—」，不编造
 * - §1.7 色阶：涨跌热力连续映射（lib/chart.heatColor）+ IV 低→高 up→brand→down
 */
import type { IvRankRow, Sector } from '@/api/types';
import { heatColor } from '@/lib/chart';

/* ---------- 类型 ---------- */
export type SourceStatus = 'active' | 'degraded' | 'stale' | 'insufficient_data';

export interface ConstituentVm {
  ticker: string;
  name: string;
  changePct: number | null;
  price: number | null;
  strengthScore: number | null;
}

export interface SectorVm {
  id: string;
  name: string;
  changePct: number | null;
  strengthScore: number | null;
  count: number;
  constituents: ConstituentVm[];
  trend30d: number[];
  ivAvg: number | null;
  rsVsSpy: number | null;
  flowRating: number | null;
  corr: { spy: number; ndx: number; ust20y: number } | null;
  asOf: string | null;
  leader: { ticker: string; changePct: number } | null;
  laggard: { ticker: string; changePct: number } | null;
}

export interface IvRowVm {
  ticker: string;
  name: string;
  price: number | null;
  rank: number | null;        // sector_iv_rank 0–100
  atmIv: number | null;       // atm_iv_percent（%）
  ivChange30d: number | null; // 契约恒 null → UI 渲染「—」
  changePct: number | null;
  stale: boolean;
  asOf: string | null;
  sourceStatus: SourceStatus;
}

/* ---------- 读取助手 ---------- */
type Rec = Record<string, unknown>;

const num = (v: unknown): number | null => (typeof v === 'number' && Number.isFinite(v) ? v : null);
const str = (v: unknown): string | null => (typeof v === 'string' && v ? v : null);
const pickNum = (r: Rec, ...keys: string[]): number | null => {
  for (const k of keys) {
    const v = num(r[k]);
    if (v !== null) return v;
  }
  return null;
};

function normalizeStatus(v: unknown): SourceStatus {
  return v === 'degraded' || v === 'stale' || v === 'insufficient_data' ? v : 'active';
}

/* ---------- 板块归一化 ---------- */
export function normalizeSector(raw: Sector): SectorVm {
  const r = raw as unknown as Rec;
  /* 成分：mock 扩展为对象数组；live 契约为 tickers: string[] */
  let constituents: ConstituentVm[] = [];
  if (Array.isArray(r.constituents)) {
    constituents = (r.constituents as Rec[]).map((c) => ({
      ticker: String(c.ticker ?? ''),
      name: String(c.name ?? ''),
      changePct: pickNum(c, 'changePct', 'change_percent'),
      price: pickNum(c, 'price'),
      strengthScore: pickNum(c, 'strengthScore', 'strength_score'),
    }));
  } else if (Array.isArray(r.tickers)) {
    constituents = (r.tickers as unknown[]).map((t) => ({
      ticker: String(t ?? ''),
      name: '',
      changePct: null,
      price: null,
      strengthScore: null,
    }));
  }
  const withChange = constituents.filter((c): c is ConstituentVm & { changePct: number } => c.changePct !== null);
  const leader = withChange.length ? withChange.reduce((a, b) => (b.changePct > a.changePct ? b : a)) : null;
  const laggard = withChange.length ? withChange.reduce((a, b) => (b.changePct < a.changePct ? b : a)) : null;
  const corrRaw = r.corr as Rec | undefined;
  return {
    id: String(raw.id ?? ''),
    name: String(raw.name ?? ''),
    changePct: pickNum(r, 'changePct', 'change_percent'),
    strengthScore: pickNum(r, 'strengthScore', 'strength_score'),
    count: constituents.length,
    constituents,
    trend30d: Array.isArray(r.trend30d) ? (r.trend30d as unknown[]).map((v) => num(v) ?? 0) : [],
    ivAvg: pickNum(r, 'ivAvg', 'iv_avg'),
    rsVsSpy: pickNum(r, 'rsVsSpy', 'rs_vs_spy'),
    flowRating: pickNum(r, 'flowRating', 'flow_rating'),
    corr:
      corrRaw && typeof corrRaw === 'object'
        ? { spy: num(corrRaw.spy) ?? 0, ndx: num(corrRaw.ndx) ?? 0, ust20y: num(corrRaw.ust20y) ?? 0 }
        : null,
    asOf: str(r.asOf) ?? str(r.as_of),
    leader: leader ? { ticker: leader.ticker, changePct: leader.changePct } : null,
    laggard: laggard ? { ticker: laggard.ticker, changePct: laggard.changePct } : null,
  };
}

/* ---------- IV 排名归一化 ---------- */
export function normalizeIvRow(raw: IvRankRow): IvRowVm {
  const r = raw as unknown as Rec;
  return {
    ticker: String(r.ticker ?? ''),
    name: String(r.name ?? ''),
    price: pickNum(r, 'price'),
    /* 契约：sector_iv_rank(0-100) 有值；iv_rank / iv_percentile 恒 null（不作为来源） */
    rank: pickNum(r, 'sector_iv_rank', 'sectorIvRank', 'ivPercentile'),
    atmIv: pickNum(r, 'atm_iv_percent', 'atmIvPercent', 'iv'),
    ivChange30d: pickNum(r, 'iv_change_30d', 'ivChange30d'),
    changePct: pickNum(r, 'changePct', 'change_percent'),
    stale: r._stale === true || r.stale === true,
    asOf: str(r.as_of) ?? str(r.asOf),
    sourceStatus: normalizeStatus(r.source_status ?? r.sourceStatus),
  };
}

/** 面板级聚合：任一行过期 → 过期横幅；source_status 取最差档 */
export function aggregateIvMeta(rows: IvRowVm[]): { stale: boolean; status: SourceStatus; asOf: string | null } {
  const order: SourceStatus[] = ['active', 'degraded', 'insufficient_data', 'stale'];
  let status: SourceStatus = 'active';
  let stale = false;
  let asOf: string | null = null;
  for (const row of rows) {
    if (row.stale) stale = true;
    if (order.indexOf(row.sourceStatus) > order.indexOf(status)) status = row.sourceStatus;
    if (!asOf && row.asOf) asOf = row.asOf;
  }
  if (stale && status === 'active') status = 'stale';
  return { stale, status, asOf };
}

/* ---------- 色阶助手 ---------- */
type Rgb = [number, number, number];

function parseRgb(css: string): Rgb | null {
  const m = css.match(/rgb\((\d+),(\d+),(\d+)\)/);
  return m ? [Number(m[1]), Number(m[2]), Number(m[3])] : null;
}

/** 相对亮度 0–1（用于深底反白判定） */
function luminance([r, g, b]: Rgb): number {
  return (0.299 * r + 0.587 * g + 0.114 * b) / 255;
}

/** 涨跌热力砖底色（§1.7 连续映射，经 lib/chart.heatColor）+ 深底判定 */
export function heatTone(pct: number): { bg: string; dark: boolean } {
  const bg = heatColor(pct);
  const rgb = parseRgb(bg);
  return { bg, dark: rgb ? luminance(rgb) < 0.55 : false };
}

/* §1.7 IV 热力色阶：低 #0E9F6E → #E4E9FF → 高 #E5484D */
const IV_STOPS: { v: number; rgb: Rgb }[] = [
  { v: 0, rgb: [14, 159, 110] },
  { v: 50, rgb: [228, 233, 255] },
  { v: 100, rgb: [229, 72, 77] },
];

export function ivRankColor(rank: number): string {
  const clamped = Math.max(0, Math.min(100, rank));
  for (let i = 0; i < IV_STOPS.length - 1; i++) {
    const a = IV_STOPS[i];
    const b = IV_STOPS[i + 1];
    if (clamped >= a.v && clamped <= b.v) {
      const t = (clamped - a.v) / (b.v - a.v);
      const mix = a.rgb.map((v, k) => Math.round(v + (b.rgb[k] - v) * t)) as Rgb;
      return `rgb(${mix[0]},${mix[1]},${mix[2]})`;
    }
  }
  return 'rgb(228,233,255)';
}

/** IV 色阶上的文字色判定（浅底用深字、深底反白） */
export function toneOnColor(bg: string): 'light' | 'dark' {
  const rgb = parseRgb(bg);
  return rgb && luminance(rgb) < 0.55 ? 'light' : 'dark';
}

/** source_status → 中文徽标文案（api-contract §2） */
export const SOURCE_STATUS_CN: Record<Exclude<SourceStatus, 'active'>, string> = {
  degraded: '数据源降级',
  stale: '数据过期',
  insufficient_data: '数据不足',
};
