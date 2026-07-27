/**
 * 大盘强弱页 mock（/market 专用）
 * - getMarketRegime：确定性六维形态 0-100，字段与 contract strength/scan 的 market_regime 同名
 * - getMarketStatusDetail：对齐 contract market/status 形状（market/phase/holiday/next_open/next_close/server_time）
 * - getIndexIntraday：确定性盘中序列生成器（仅 mock；live 无指数K线端点，UI 如实留空）
 */
import { Rng, round2 } from './rng';
import { t as __t } from '../i18n/core.ts';

/* ---------------- 市场形态六维（contract market_regime 同名） ---------------- */
export interface MarketRegime {
  index_trend_score: number;
  market_momentum_score: number;
  market_breadth_score: number;
  market_volume_score: number;
  risk_appetite_score: number;
  risk_on_spread_score: number;
}

export function getMarketRegime(): MarketRegime {
  const r = new Rng(61423);
  const base = r.normal(58, 12, 36, 82);
  const mk = (off: number) => Math.round(Math.min(96, Math.max(8, base + r.float(-18, 18) + off)));
  return {
    index_trend_score: mk(6),
    market_momentum_score: mk(2),
    market_breadth_score: mk(0),
    market_volume_score: mk(-4),
    risk_appetite_score: mk(3),
    risk_on_spread_score: mk(-2),
  };
}

/* ---------------- 市场状态（contract market/status 形状） ---------------- */
export interface MarketStatusDetail {
  market: 'open' | 'premarket' | 'postmarket' | 'closed';
  phase: string | null;        // 时段说明文案
  holiday: string | null;      // 节假日名称（mock 无节假日日历 → null，UI 显示「—」）
  next_open: string | null;    // ISO（真实 UTC 瞬时）
  next_close: string | null;
  server_time: string;         // ET ISO
}

const ET = 'America/New_York';

function etParts(d: Date): { y: number; mo: number; d: number; h: number; mi: number; s: number } {
  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone: ET,
    hour12: false,
    year: 'numeric',
    month: 'numeric',
    day: 'numeric',
    hour: 'numeric',
    minute: 'numeric',
    second: 'numeric',
  }).formatToParts(d);
  const get = (t: string) => Number(parts.find((p) => p.type === t)?.value ?? 0);
  const h = get('hour') % 24;
  return { y: get('year'), mo: get('month'), d: get('day'), h, mi: get('minute'), s: get('second') };
}

/** ET 墙钟 → 真实 UTC 毫秒（Intl 反推偏移，一次修正） */
function etWallToUtc(y: number, mo: number, d: number, h: number, mi: number): number {
  const guess = Date.UTC(y, mo - 1, d, h, mi, 0);
  const p = etParts(new Date(guess));
  const asUtc = Date.UTC(p.y, p.mo - 1, p.d, p.h, p.mi, p.s);
  return guess + (guess - asUtc);
}

/** 下一个工作日 h:mi ET 的真实瞬时 ISO */
function nextWeekdayEt(nowMs: number, h: number, mi: number): string {
  const p = etParts(new Date(nowMs));
  let { y, mo, d } = p;
  for (let i = 0; i < 8; i++) {
    const ts = etWallToUtc(y, mo, d, h, mi);
    const weekday = new Date(Date.UTC(y, mo - 1, d)).getUTCDay();
    if (ts > nowMs && weekday >= 1 && weekday <= 5) return new Date(ts).toISOString();
    const next = new Date(Date.UTC(y, mo - 1, d + 1));
    y = next.getUTCFullYear();
    mo = next.getUTCMonth() + 1;
    d = next.getUTCDate();
  }
  return new Date(nowMs + 24 * 3_600_000).toISOString();
}

export function getMarketStatusDetail(nowMs = Date.now()): MarketStatusDetail {
  const p = etParts(new Date(nowMs));
  const weekday = new Date(Date.UTC(p.y, p.mo - 1, p.d)).getUTCDay();
  const isWeekday = weekday >= 1 && weekday <= 5;
  const mins = p.h * 60 + p.mi;
  let market: MarketStatusDetail['market'] = 'closed';
  let phase: string;
  if (isWeekday && mins >= 240 && mins < 570) {
    market = 'premarket';
    phase = '盘前交易 · 流动性较薄，报价点差偏大';
  } else if (isWeekday && mins >= 570 && mins < 960) {
    market = 'open';
    phase = '常规交易时段 · 9:30–16:00 ET';
  } else if (isWeekday && mins >= 960 && mins < 1200) {
    market = 'postmarket';
    phase = '盘后交易 · 留意财报与公告驱动';
  } else {
    phase = isWeekday ? __t('常规时段已收盘 · 等待下一交易时段') : __t('周末休市 · 下一个交易日 9:30 ET 开盘');
  }
  return {
    market,
    phase,
    holiday: null,
    next_open: nextWeekdayEt(nowMs, 9, 30),
    next_close: nextWeekdayEt(nowMs, 16, 0),
    server_time: new Date(nowMs).toISOString(),
  };
}

/* ---------------- 指数盘中序列（确定性，仅 mock） ---------------- */
function seedOf(code: string): number {
  let s = 0;
  for (const ch of code) s = (s * 31 + ch.charCodeAt(0)) >>> 0;
  return s;
}

/**
 * 盘中分时（相对昨收 %，39 点 ≈ 10 分钟一格）
 * 末点收敛到 changePct，保证与卡片涨跌一致（design.md §11 真实性约束）。
 */
export function getIndexIntraday(code: string, changePct: number): number[] {
  const r = new Rng(seedOf(code) ^ 0x9e3779b9);
  const n = 39;
  const vol = code === 'VIX' ? 0.9 : 0.32;
  const walk: number[] = [0];
  for (let i = 1; i < n; i++) walk.push(walk[i - 1] + r.float(-vol, vol));
  const drift = changePct - walk[n - 1];
  return walk.map((v, i) => round2(v + (drift * i) / (n - 1)));
}
