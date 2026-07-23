/**
 * 大盘强弱契约网关（/market 页专用）
 * - mock：走 fixtures + marketPulse（确定性种子，形状 1:1 对齐 api-contract.md）
 * - live：走 /api/market/status 与 /api/strength/market（market_regime）
 * 注：脚手架 src/api/modules/market.ts 为精简形状且按约束不可改动，
 *     本网关复用 client 的 mockOr/get 模式，不改动 api 层。
 */
import { get, mockOr } from '@/api/client';
import * as pulse from '@/mocks/marketPulse';
import type { MarketRegime, MarketStatusDetail } from '@/mocks/marketPulse';

export type { MarketRegime, MarketStatusDetail } from '@/mocks/marketPulse';

interface RawMarketStatus {
  market?: MarketStatusDetail['market'];
  phase?: string | null;
  holiday?: string | null;
  next_open?: string | null;
  next_close?: string | null;
  server_time?: string;
}

export const marketPulseApi = {
  /** GET /api/market/status（contract：market/phase/holiday/next_open/next_close/server_time） */
  statusDetail: (): Promise<MarketStatusDetail> =>
    mockOr(
      () => pulse.getMarketStatusDetail(),
      async () => {
        const raw = await get<RawMarketStatus>('/market/status');
        return {
          market: raw?.market ?? 'closed',
          phase: raw?.phase ?? null,
          holiday: raw?.holiday ?? null,
          next_open: raw?.next_open ?? null,
          next_close: raw?.next_close ?? null,
          server_time: raw?.server_time ?? '',
        };
      },
    ),
  /**
   * GET /api/strength/market → market_regime 六维
   * live 未覆盖该字段时返回 null，UI 显示「快照不可用」（留空优于编造）。
   */
  regime: (): Promise<MarketRegime | null> =>
    mockOr(
      () => pulse.getMarketRegime(),
      async () => {
        const raw = await get<{ market_regime?: MarketRegime | null }>('/strength/market');
        return raw?.market_regime ?? null;
      },
    ),
};
