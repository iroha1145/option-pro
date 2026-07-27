/** 市场域：GET /api/market/indices · GET /api/market/status */
import {mockOr} from '../client';
import { resetSharedReads, sharedGlobalGet } from '../sharedRead';
import { asRec, pickN, pickS, unwrap } from '../live';
import * as fx from '@/mocks/fixtures';
import type { IndexQuote, MarketSession, MarketStatus } from '../types';
import { t } from '../../i18n/core.ts';

/**
 * Yahoo 风格指数符号 → UI 短代码 + 中文名（纸带与 /market 指数卡共用此映射）。
 * 未知符号原样透传（code=name=symbol），不编造。
 */
const INDEX_SYMBOL_MAP: Record<string, { code: string; name: string }> = {
  '^GSPC': { code: 'SPX', name: t('标普500') },
  '^IXIC': { code: 'IXIC', name: t('纳指综合') },
  '^NDX': { code: 'NDX', name: t('纳指100') },
  '^DJI': { code: 'DJI', name: t('道琼斯') },
  '^RUT': { code: 'RUT', name: t('罗素2000') },
  '^N225': { code: 'N225', name: t('日经225') },
  '000001.SS': { code: 'SSE', name: t('上证综指') },
  '^VIX': { code: 'VIX', name: t('恐慌指数') },
  '^SOX': { code: 'SOX', name: t('费城半导体') },
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
  open: { session: 'regular', label: t('盘中') },
  'pre-market': { session: 'premarket', label: t('盘前') },
  premarket: { session: 'premarket', label: t('盘前') },
  'after-hours': { session: 'afterhours', label: t('盘后') },
  postmarket: { session: 'afterhours', label: t('盘后') },
  closed: { session: 'closed', label: t('休市') },
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

/* 共享落在原始响应层（api/sharedRead）：components/market/api.ts 用自己的
   mapper 拉同一批端点，只共享映射结果覆盖不到它。 */
const sharedStatus = (): Promise<MarketStatus> =>
  sharedGlobalGet<unknown>('/market/status').then(mapStatus);

const sharedIndices = (): Promise<IndexQuote[]> =>
  sharedGlobalGet<unknown>('/market/indices').then(mapIndices);

/** 测试用复位；生产依赖有界过期。 */
export function resetMarketStatusShare(): void {
  resetSharedReads();
}

export const marketApi = {
  /* 常驻的 IndexTape 与大盘页会同时要这份数据；共享同一次请求。 */
  indices: (): Promise<IndexQuote[]> => mockOr(() => fx.getIndices(), sharedIndices),
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
