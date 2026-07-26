import type { OptionAlertInput } from '../../api/modules/ai-jobs.ts';
import type { OptionChain, OptionChainRow } from '../../api/types.ts';
import { t } from '../../i18n/core.ts';

export interface OptionAlertResult {
  output_language: 'zh-CN';
  confidence: 'high' | 'medium' | 'low';
  direction: 'bullish' | 'bearish' | 'mixed' | 'unknown';
  direction_status: 'available' | 'unavailable_without_trade_side';
  summary: string;
  analysis: string;
  key_strikes: string[];
  risk_note: string;
}

interface RankedAlert {
  alert: OptionAlertInput;
  severity: number;
  premium: number;
  /** 量持比；持仓量为 0 或不可用时没有比值可言，恒为 null（不是 0）。 */
  ratio: number | null;
}

function finiteNonnegative(value: number | null): number | null {
  return value !== null && Number.isFinite(value) && value >= 0 ? value : null;
}

export function midpoint(
  bid: number | null,
  ask: number | null,
): number | null {
  const safeBid = finiteNonnegative(bid);
  const safeAsk = finiteNonnegative(ask);
  if (safeBid === null || safeAsk === null || safeAsk < safeBid) return null;
  if (safeBid === 0 && safeAsk === 0) return null;
  return (safeBid + safeAsk) / 2;
}

/**
 * 量持比的四种状态（GPT-5.6-Pro 审计 P1-03）。
 * 旧实现把「持仓量为 0」折叠成比值 0，于是新挂牌合约、隔夜持仓量归零、大量新开仓
 * 这类最值得关注的情况永远无法触发 ratio > 3。这里把它们分开：
 * ratio 只在持仓量为正时有值，持仓量为 0 且有成交是独立的「新开仓候选」。
 */
export type VolOiState =
  | { kind: 'ratio'; ratio: number }
  | { kind: 'new_opening' }
  | { kind: 'no_activity' }
  | { kind: 'unavailable' };

export function volOiState(
  volume: number | null,
  openInterest: number | null,
): VolOiState {
  const safeVolume = finiteNonnegative(volume);
  const safeOpenInterest = finiteNonnegative(openInterest);
  if (safeOpenInterest === null) return { kind: 'unavailable' };
  if (safeOpenInterest > 0) {
    if (safeVolume === null) return { kind: 'unavailable' };
    return { kind: 'ratio', ratio: safeVolume / safeOpenInterest };
  }
  if (safeVolume === null) return { kind: 'unavailable' };
  return safeVolume > 0 ? { kind: 'new_opening' } : { kind: 'no_activity' };
}

function moneyness(
  type: 'call' | 'put',
  strike: number,
  spot: number | null,
): OptionAlertInput['moneyness'] {
  if (spot === null || !Number.isFinite(spot) || spot <= 0) return 'unavailable';
  if (strike === spot) return 'atm';
  if (type === 'call') return strike > spot ? 'otm' : 'itm';
  return strike < spot ? 'otm' : 'itm';
}

function buildLeg(
  row: OptionChainRow,
  type: 'call' | 'put',
  spot: number | null,
  expiration: string,
  daysToExpiry: number,
): RankedAlert | null {
  const strike = finiteNonnegative(row.strike);
  if (strike === null || strike <= 0) return null;

  const volumeRaw = type === 'call' ? row.callVol : row.putVol;
  const openInterestRaw = type === 'call' ? row.callOi : row.putOi;
  const bid = type === 'call' ? row.callBid : row.putBid;
  const ask = type === 'call' ? row.callAsk : row.putAsk;
  const impliedVolatilityRaw = type === 'call' ? row.callIv : row.putIv;
  const volumeValue = finiteNonnegative(volumeRaw);
  // 成交量缺失时无法判定异动；这与「成交量为 0」都不进入证据集，但原因不同。
  if (volumeValue === null) return null;
  const volume = Math.max(0, Math.round(volumeValue));
  const openInterestValue = finiteNonnegative(openInterestRaw);
  const openInterest =
    openInterestValue === null ? null : Math.max(0, Math.round(openInterestValue));
  if (volume <= 0) return null;

  const state = volOiState(volume, openInterest);
  const ratio = state.kind === 'ratio' ? state.ratio : null;
  const mid = midpoint(bid, ask);
  const premium = mid === null ? 0 : volume * mid * 100;
  const legMoneyness = moneyness(type, strike, spot);
  const distance =
    spot !== null && Number.isFinite(spot) && spot > 0
      ? Math.abs(strike - spot) / spot
      : 0;
  const reasons: string[] = [];
  let severity = 0;

  if (ratio !== null && ratio >= 3) {
    severity += 4;
    reasons.push(t('成交量/持仓量 {ratio} 倍', { ratio: ratio.toFixed(1) }));
  }
  // 持仓量为 0 而当日有成交：全部是新开仓，量持比无穷大而不是 0。
  if (state.kind === 'new_opening') {
    severity += 4;
    reasons.push(t('持仓量为 0 且成交 {volume} 张，全部为新开仓（量持比不适用）', { volume }));
  }
  if (volume >= 5_000) {
    severity += 3;
    reasons.push(t('成交量 {volume} 张', { volume }));
  }
  if (premium >= 500_000) {
    severity += 2;
    reasons.push(t('按买卖中价估算权利金 {premium} 美元', { premium: Math.round(premium) }));
  }
  if (volume >= 1_000 && openInterest !== null && openInterest > 0 && openInterest < 500) {
    severity += 2;
    reasons.push(t('成交量较高且持仓量较低，待后续持仓量确认'));
  }
  if (volume >= 1_000 && openInterest === null) {
    severity += 1;
    reasons.push(t('持仓量不可用，无法计算量持比'));
  }
  if (legMoneyness === 'otm' && distance > 0.1 && volume >= 2_000) {
    severity += 1;
    reasons.push(t('深度虚值约 {pct}%', { pct: (distance * 100).toFixed(0) }));
  }
  if (reasons.length === 0) return null;

  const impliedVolatility = finiteNonnegative(impliedVolatilityRaw);
  const alert: OptionAlertInput = {
    strike,
    type,
    expiration,
    dte: Math.max(0, Math.round(daysToExpiry)),
    volume,
    // 持仓量不可用时不发送该字段：后端已把它建模为可空，补 0 会让模型
    // 把「没读到」当成「确实是 0」。
    ...(openInterest !== null ? { open_interest: openInterest } : {}),
    ...(impliedVolatility !== null && impliedVolatility <= 100
      ? { implied_volatility: impliedVolatility }
      : {}),
    ...(premium > 0 ? { premium_flow: Math.round(premium) } : {}),
    ...(ratio !== null ? { vol_oi_ratio: Number(ratio.toFixed(2)) } : {}),
    reasons,
    signal: 'unknown',
    inferred_direction: 'unknown',
    moneyness: legMoneyness,
    direction: 'unknown',
    direction_confidence: 0,
    direction_status: 'unavailable_without_trade_side',
    direction_deprecated: true,
    direction_note: t('缺少成交主动方，无法判断真实交易方向'),
  };
  return { alert, severity, premium, ratio };
}

/**
 * 从当前屏幕使用的真实期权链提取最多十条异动证据。
 * 只使用链中已有字段；买卖中价计算的权利金会在 reasons 中明确标成估算。
 */
export function buildOptionAlertEvidence(
  chain: OptionChain,
  expiration: string,
  daysToExpiry: number,
): OptionAlertInput[] {
  const ranked = chain.rows.flatMap((row) =>
    (['call', 'put'] as const)
      .map((type) =>
        buildLeg(row, type, chain.spot, expiration, daysToExpiry),
      )
      .filter((item): item is RankedAlert => item !== null),
  );
  // 量持比缺失（新开仓或持仓量不可用）在同分位排在有比值的后面，而不是当成 0
  // 参与大小比较 —— 后者会把「无比值」排到所有真实低比值之前。
  const ratioRank = (item: RankedAlert): number =>
    item.ratio === null ? -1 : item.ratio;
  ranked.sort(
    (left, right) =>
      right.severity - left.severity ||
      ratioRank(right) - ratioRank(left) ||
      right.premium - left.premium ||
      right.alert.volume - left.alert.volume ||
      left.alert.strike - right.alert.strike ||
      left.alert.type.localeCompare(right.alert.type),
  );
  return ranked.slice(0, 10).map((item) => item.alert);
}

function recordOf(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function nonemptyText(value: unknown): string | null {
  return typeof value === 'string' && value.trim() ? value.trim() : null;
}

/** 校验后端 option_alerts 结构；缺字段时不拼接任何替代结论。 */
export function parseOptionAlertResult(result: unknown): OptionAlertResult | null {
  const record = recordOf(result);
  if (!record || record.output_language !== 'zh-CN') return null;
  const confidence = record.confidence;
  const direction = record.direction;
  const directionStatus = record.direction_status;
  const summary = nonemptyText(record.summary);
  const analysis = nonemptyText(record.analysis);
  const riskNote = nonemptyText(record.risk_note);
  const keyStrikes = Array.isArray(record.key_strikes)
    ? record.key_strikes.map(nonemptyText)
    : [];
  if (
    !['high', 'medium', 'low'].includes(String(confidence)) ||
    !['bullish', 'bearish', 'mixed', 'unknown'].includes(String(direction)) ||
    !['available', 'unavailable_without_trade_side'].includes(
      String(directionStatus),
    ) ||
    !summary ||
    !analysis ||
    !riskNote ||
    keyStrikes.some((item) => item === null)
  ) {
    return null;
  }
  return {
    output_language: 'zh-CN',
    confidence: confidence as OptionAlertResult['confidence'],
    direction: direction as OptionAlertResult['direction'],
    direction_status:
      directionStatus as OptionAlertResult['direction_status'],
    summary,
    analysis,
    key_strikes: keyStrikes as string[],
    risk_note: riskNote,
  };
}
