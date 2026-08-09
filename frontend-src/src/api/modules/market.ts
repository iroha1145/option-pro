/** 市场域：GET /api/market/indices · GET /api/market/status · GET /api/market/cta */
import {mockOr} from '../client';
import { marketGet } from '../marketRead';
import { resetSharedReads, sharedGlobalGet } from '../sharedRead';
import { asRec, pickN, pickS, unwrap, type Rec } from '../live';
import * as fx from '@/mocks/fixtures';
import * as fx2 from '@/mocks/fixtures2';
import type {
  CtaInstrumentEstimate,
  CtaTrendPayload,
  CtaTriggerKind,
  CtaTriggerZone,
  IndexQuote,
  MarketSession,
  MarketStatus,
} from '../types';
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
      // 真实符号必须随行保留：详情页与全部 /stocks 端点只认 ^GSPC，
      // 拿显示短码 SPX 去开详情会整页报「行情服务暂不可用」。
      symbol,
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

/** 契约 /market/cta → CtaTrendPayload（null 保真：缺数据不折 0/中性） */
export function mapCtaTrend(body: unknown): CtaTrendPayload {
  const r = asRec(body);
  const zone = (item: Rec): CtaTriggerZone | null => {
    const id = pickS(item, 'id');
    const labelKey = pickS(item, 'label_key');
    const kindRaw = pickS(item, 'kind');
    const price = pickN(item, 'price');
    if (id === null || labelKey === null || price === null) return null;
    const kind: CtaTriggerKind =
      kindRaw === 'trend_cross' ||
      kindRaw === 'trend_saturation' ||
      kindRaw === 'trend_cross_and_saturation' ||
      kindRaw === 'vol_delever' ||
      kindRaw === 'mixed'
        ? kindRaw
        : 'trend_flip';
    return {
      id,
      rank: pickN(item, 'rank') ?? 0,
      label_key: labelKey,
      kind,
      price,
      price_low: pickN(item, 'price_low') ?? price,
      price_high: pickN(item, 'price_high') ?? price,
      distance_pct: pickN(item, 'distance_pct') ?? 0,
      nearest_event_distance_pct: pickN(item, 'nearest_event_distance_pct'),
      models: unwrap(item, 'models').flatMap((v) => (typeof v === 'string' ? [v] : [])),
      components: unwrap(item, 'components').flatMap((v) => (typeof v === 'string' ? [v] : [])),
      event_types: unwrap(item, 'event_types').flatMap((v) => (typeof v === 'string' ? [v] : [])),
      weight_share: pickN(item, 'weight_share') ?? 0,
      est_position_change: pickN(item, 'est_position_change') ?? 0,
      trend_change: pickN(item, 'trend_change') ?? 0,
      vol_change: pickN(item, 'vol_change') ?? 0,
      position_before: pickN(item, 'position_before'),
      position_after: pickN(item, 'position_after'),
      trend_before: pickN(item, 'trend_before'),
      trend_after: pickN(item, 'trend_after'),
      needs_close_confirm: item.needs_close_confirm !== false,
    };
  };
  const instruments = unwrap(r, 'instruments').map((item): CtaInstrumentEstimate => {
    const rec = asRec(item);
    const submodelsRaw = rec.submodels === null || rec.submodels === undefined ? null : asRec(rec.submodels);
    const submodels = submodelsRaw === null
      ? null
      : Object.fromEntries(
          Object.entries(submodelsRaw).flatMap(([key, value]) => {
            const sub = asRec(value);
            const label = pickS(sub, 'label');
            const weight = pickN(sub, 'weight');
            const signal = pickN(sub, 'signal');
            if (label === null || weight === null || signal === null) return [];
            return [[key, { label, weight, signal }]];
          }),
        );
    const volRaw = rec.volatility === null || rec.volatility === undefined ? null : asRec(rec.volatility);
    const triggersRaw = rec.trigger_levels === null || rec.trigger_levels === undefined ? null : asRec(rec.trigger_levels);
    const curveRaw = rec.scenario_curve === null || rec.scenario_curve === undefined ? null : asRec(rec.scenario_curve);
    const intradayRaw = rec.intraday === null || rec.intraday === undefined ? null : asRec(rec.intraday);
    const coverageRaw = rec.coverage === null || rec.coverage === undefined ? null : asRec(rec.coverage);
    const numbers = (raw: Rec, key: string): number[] =>
      unwrap(raw, key).flatMap((v) => (typeof v === 'number' && Number.isFinite(v) ? [v] : []));
    return {
      instrument: pickS(rec, 'instrument') ?? '',
      label: pickS(rec, 'label') ?? '',
      proxy_symbol: pickS(rec, 'proxy_symbol') ?? '',
      proxy_type: pickS(rec, 'proxy_type') ?? 'etf',
      index_symbol: pickS(rec, 'index_symbol') ?? '',
      source_status: pickS(rec, 'source_status') ?? 'unavailable',
      settlement_confirmed: typeof rec.settlement_confirmed === 'boolean' ? rec.settlement_confirmed : null,
      position_score: pickN(rec, 'position_score'),
      previous_position_score: pickN(rec, 'previous_position_score'),
      flow_score: pickN(rec, 'flow_score'),
      trend_flow: pickN(rec, 'trend_flow'),
      volatility_flow: pickN(rec, 'volatility_flow'),
      state: pickS(rec, 'state'),
      position_label: pickS(rec, 'position_label'),
      model_agreement: pickN(rec, 'model_agreement'),
      /* v2 拆解读数：一致度只表方向，强弱与表态覆盖单独下发 */
      trend_strength: pickN(rec, 'trend_strength'),
      active_model_weight: pickN(rec, 'active_model_weight'),
      /* v3：同向/表态计数由后端下发（前端不再用硬编码 0.1 阈值重算） */
      aligned_models: pickN(rec, 'aligned_models'),
      active_models: pickN(rec, 'active_models'),
      market_data_current:
        typeof rec.market_data_current === 'boolean' ? rec.market_data_current : null,
      submodels,
      volatility: volRaw === null
        ? null
        : {
            realized_annual: pickN(volRaw, 'realized_annual'),
            target_annual: pickN(volRaw, 'target_annual') ?? 0.15,
            scalar: pickN(volRaw, 'scalar') ?? 1,
            previous_scalar: pickN(volRaw, 'previous_scalar') ?? 1,
          },
      trigger_levels: triggersRaw === null
        ? null
        : {
            above: unwrap(triggersRaw, 'above').flatMap((z) => {
              const mapped = zone(asRec(z));
              return mapped ? [mapped] : [];
            }),
            below: unwrap(triggersRaw, 'below').flatMap((z) => {
              const mapped = zone(asRec(z));
              return mapped ? [mapped] : [];
            }),
          },
      scenario_curve: curveRaw === null
        ? null
        : {
            prices: numbers(curveRaw, 'prices'),
            full: numbers(curveRaw, 'full'),
            trend_only: numbers(curveRaw, 'trend_only'),
          },
      history: unwrap(rec, 'history').flatMap((row) => {
        const item2 = asRec(row);
        const date = pickS(item2, 'date');
        const position = pickN(item2, 'position');
        return date !== null && position !== null ? [{ date, position }] : [];
      }),
      reference_price: pickN(rec, 'reference_price'),
      data_through: pickS(rec, 'data_through'),
      coverage: coverageRaw === null
        ? null
        : { bars: pickN(coverageRaw, 'bars') ?? 0, required: pickN(coverageRaw, 'required') ?? 0 },
      warnings: unwrap(rec, 'warnings').flatMap((v) => (typeof v === 'string' ? [v] : [])),
      intraday: intradayRaw === null
        ? null
        : {
            price: pickN(intradayRaw, 'price') ?? 0,
            date: pickS(intradayRaw, 'date'),
            provisional: intradayRaw.provisional === true,
            crossed_zone_ids: unwrap(intradayRaw, 'crossed_zone_ids').flatMap((v) =>
              typeof v === 'string' ? [v] : [],
            ),
          },
    };
  });
  return {
    method_version: pickS(r, 'method_version') ?? '',
    generated_at: pickS(r, 'generated_at'),
    proxy_note: pickS(r, 'proxy_note') ?? 'etf_trend_proxy',
    source_status: pickS(r, 'source_status') ?? 'unavailable',
    instruments,
    /* 快照新鲜度原样透传：访客通道的 _stale/degraded 是常驻标记
       （public_snapshot_only），页面据 stale_reason 与 market_data_current
       分辨「数据过期」与「快照按墙钟变旧」，不再一律吓人。 */
    snapshot_saved_at: pickS(r, 'snapshot_saved_at'),
    stale_reason: pickS(r, 'stale_reason'),
    ...(r._stale === true ? { _stale: true } : {}),
  };
}

export const marketApi = {
  /* 常驻的 IndexTape 与大盘页会同时要这份数据；共享同一次请求。 */
  indices: (): Promise<IndexQuote[]> => mockOr(() => fx.getIndices(), sharedIndices),
  /** CTA 趋势资金代理估算：worker 快照只读（5 分钟轮询足够，日频模型）。 */
  ctaTrend: (): Promise<CtaTrendPayload> =>
    mockOr(
      () => fx2.getCtaTrend(),
      () => marketGet('/market/cta', { ttlMs: 5 * 60_000, staleMs: 60 * 60_000 }).then(mapCtaTrend),
    ),
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
