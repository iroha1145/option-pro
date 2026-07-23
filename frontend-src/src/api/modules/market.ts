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
  return unwrap(body, 'indices').map((r) => {
    const price = pickN(r, 'price') ?? 0;
    const changePct = pickN(r, 'change_percent', 'changePct') ?? 0;
    // change 由 price 与 change_percent 反推（真实算术，非编造）
    const change = Math.round(((price * changePct) / (100 + changePct)) * 100) / 100;
    const symbol = pickS(r, 'code', 'symbol') ?? '';
    const mapped = INDEX_SYMBOL_MAP[symbol];
    return {
      code: mapped?.code ?? symbol,
      name: mapped?.name ?? pickS(r, 'name') ?? symbol,
      price,
      change,
      changePct,
    };
  });
}

/** 契约 market ∈ open|premarket|postmarket|closed → UI MarketSession */
const SESSION_MAP: Record<string, { session: MarketSession; label: string }> = {
  open: { session: 'regular', label: '盘中' },
  premarket: { session: 'premarket', label: '盘前' },
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

export const marketApi = {
  indices: (): Promise<IndexQuote[]> => mockOr(() => fx.getIndices(), () => get('/market/indices').then(mapIndices)),
  status: (): Promise<MarketStatus> => mockOr(() => fx.getMarketStatus(), () => get('/market/status').then(mapStatus)),
};
