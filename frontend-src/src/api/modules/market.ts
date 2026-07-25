/** 市场域：GET /api/market/indices · GET /api/market/status */
import { get, mockOr } from '../client';
import { asRec, pickN, pickS, unwrap } from '../live';
import * as fx from '@/mocks/fixtures';
import type { IndexQuote, MarketSession, MarketStatus } from '../types';

/**
 * Yahoo 风格指数符号 → UI 短代码 + 中文名（纸带与 /market 指数卡共用此映射）。
 * 未知符号原样透传（code=name=symbol），不编造。
 */
const INDEX_SYMBOL_MAP: Record<string, { code: string; name: string }> = {
  '^GSPC': { code: 'SPX', name: '标普500' },
  '^IXIC': { code: 'IXIC', name: '纳指综合' },
  '^NDX': { code: 'NDX', name: '纳指100' },
  '^DJI': { code: 'DJI', name: '道琼斯' },
  '^RUT': { code: 'RUT', name: '罗素2000' },
  '^N225': { code: 'N225', name: '日经225' },
  '000001.SS': { code: 'SSE', name: '上证综指' },
  '^VIX': { code: 'VIX', name: '恐慌指数' },
  '^SOX': { code: 'SOX', name: '费城半导体' },
};

/** 契约 {indices:[{symbol, price, change_percent}], ...} → UI IndexQuote[] */
function mapIndices(body: unknown): IndexQuote[] {
  return unwrap(body, 'indices').flatMap((r) => {
    const price = pickN(r, 'price');
    const changePct = pickN(r, 'change_percent', 'changePct');
    // 后端允许单个指数失败并返回 null；该行应隐藏，不能冒充为 0。
    if (price === null || changePct === null) return [];
    // change 由 price 与 change_percent 反推（真实算术，非编造）
    const change = Math.round(((price * changePct) / (100 + changePct)) * 100) / 100;
    const symbol = pickS(r, 'code', 'symbol') ?? '';
    const mapped = INDEX_SYMBOL_MAP[symbol];
    return [{
      code: mapped?.code ?? symbol,
      name: mapped?.name ?? pickS(r, 'name') ?? symbol,
      price,
      change,
      changePct,
    }];
  });
}

/** 契约 market ∈ open|pre-market|after-hours|closed；兼容旧别名。 */
const SESSION_MAP: Record<string, { session: MarketSession; label: string }> = {
  open: { session: 'regular', label: '盘中' },
  'pre-market': { session: 'premarket', label: '盘前' },
  premarket: { session: 'premarket', label: '盘前' },
  'after-hours': { session: 'afterhours', label: '盘后' },
  postmarket: { session: 'afterhours', label: '盘后' },
  closed: { session: 'closed', label: '休市' },
};

/** 契约 {market, phase, next_open, next_close, server_time(ET iso), ...} → UI MarketStatus */
function mapStatus(body: unknown): MarketStatus {
  const r = asRec(body);
  const m = SESSION_MAP[pickS(r, 'market', 'session') ?? 'closed'] ?? SESSION_MAP.closed;
  const nextOpen = pickS(r, 'next_open');
  const nextClose = pickS(r, 'next_close');
  return {
    session: m.session,
    label: m.label,
    nyTime: pickS(r, 'nyTime', 'server_time') ?? '',
    nextEvent:
      m.session === 'regular' && nextClose
        ? { kind: 'close', at: nextClose }
        : nextOpen
          ? { kind: 'open', at: nextOpen }
          : nextClose
            ? { kind: 'close', at: nextClose }
            : null,
  };
}

/**
 * 同一时刻只允许一个 /market/status 在飞，并把结果短暂共享。
 * 只作用于这一个只读端点：它对所有调用方返回同一个全局事实，没有按用户或
 * 按参数的差异，因此共享不会串数据。
 */
const STATUS_SHARE_MS = 2_000;
let statusInFlight: Promise<MarketStatus> | null = null;
let statusValue: { at: number; value: MarketStatus } | null = null;

function sharedStatus(): Promise<MarketStatus> {
  const now = Date.now();
  if (statusValue && now - statusValue.at < STATUS_SHARE_MS) {
    return Promise.resolve(statusValue.value);
  }
  if (statusInFlight) return statusInFlight;
  const request = get('/market/status')
    .then(mapStatus)
    .then((value) => {
      statusValue = { at: Date.now(), value };
      return value;
    })
    .finally(() => {
      if (statusInFlight === request) statusInFlight = null;
    });
  statusInFlight = request;
  return request;
}

/** 测试用复位；生产依赖上面的有界过期。 */
export function resetMarketStatusShare(): void {
  statusInFlight = null;
  statusValue = null;
}

export const marketApi = {
  indices: (): Promise<IndexQuote[]> => mockOr(() => fx.getIndices(), () => get('/market/indices').then(mapIndices)),
  /**
   * 市场时段。
   *
   * 页面上有三个各自独立的 usePolling 在拉这一个接口（Navbar、自选页两处），
   * 实测首屏因此发出 3 次 /market/status。它们要的是同一个事实，所以在这里做
   * 短窗口共享：并发调用共用同一个请求，2 秒内的重复调用复用同一份结果。
   * 各自的轮询周期不变，因此「多久算过期」的语义没有改变。
   */
  status: (): Promise<MarketStatus> =>
    mockOr(() => fx.getMarketStatus(), sharedStatus),
};
