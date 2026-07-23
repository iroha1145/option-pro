import type { OptionAlertInput } from '../../api/modules/ai-jobs.ts';
import type { OptionChain, OptionChainRow } from '../../api/types.ts';

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
  ratio: number;
}

function finiteNonnegative(value: number): number | null {
  return Number.isFinite(value) && value >= 0 ? value : null;
}

function midpoint(bid: number, ask: number): number | null {
  const safeBid = finiteNonnegative(bid);
  const safeAsk = finiteNonnegative(ask);
  if (safeBid === null || safeAsk === null || safeAsk < safeBid) return null;
  if (safeBid === 0 && safeAsk === 0) return null;
  return (safeBid + safeAsk) / 2;
}

function moneyness(
  type: 'call' | 'put',
  strike: number,
  spot: number,
): OptionAlertInput['moneyness'] {
  if (!Number.isFinite(spot) || spot <= 0) return 'unavailable';
  if (strike === spot) return 'atm';
  if (type === 'call') return strike > spot ? 'otm' : 'itm';
  return strike < spot ? 'otm' : 'itm';
}

function buildLeg(
  row: OptionChainRow,
  type: 'call' | 'put',
  spot: number,
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
  const volume = Math.max(0, Math.round(finiteNonnegative(volumeRaw) ?? 0));
  const openInterest = Math.max(
    0,
    Math.round(finiteNonnegative(openInterestRaw) ?? 0),
  );
  if (volume <= 0) return null;

  const ratio = openInterest > 0 ? volume / openInterest : 0;
  const mid = midpoint(bid, ask);
  const premium = mid === null ? 0 : volume * mid * 100;
  const legMoneyness = moneyness(type, strike, spot);
  const distance =
    Number.isFinite(spot) && spot > 0 ? Math.abs(strike - spot) / spot : 0;
  const reasons: string[] = [];
  let severity = 0;

  if (ratio >= 3) {
    severity += 4;
    reasons.push(`成交量/持仓量 ${ratio.toFixed(1)} 倍`);
  }
  if (volume >= 5_000) {
    severity += 3;
    reasons.push(`成交量 ${volume} 张`);
  }
  if (premium >= 500_000) {
    severity += 2;
    reasons.push(`按买卖中价估算权利金 ${Math.round(premium)} 美元`);
  }
  if (volume >= 1_000 && openInterest < 500) {
    severity += 2;
    reasons.push('成交量较高且持仓量较低，待后续持仓量确认');
  }
  if (legMoneyness === 'otm' && distance > 0.1 && volume >= 2_000) {
    severity += 1;
    reasons.push(`深度虚值约 ${(distance * 100).toFixed(0)}%`);
  }
  if (reasons.length === 0) return null;

  const impliedVolatility = finiteNonnegative(impliedVolatilityRaw);
  const alert: OptionAlertInput = {
    strike,
    type,
    expiration,
    dte: Math.max(0, Math.round(daysToExpiry)),
    volume,
    open_interest: openInterest,
    ...(impliedVolatility !== null && impliedVolatility <= 100
      ? { implied_volatility: impliedVolatility }
      : {}),
    ...(premium > 0 ? { premium_flow: Math.round(premium) } : {}),
    ...(openInterest > 0 ? { vol_oi_ratio: Number(ratio.toFixed(2)) } : {}),
    reasons,
    signal: 'unknown',
    inferred_direction: 'unknown',
    moneyness: legMoneyness,
    direction: 'unknown',
    direction_confidence: 0,
    direction_status: 'unavailable_without_trade_side',
    direction_deprecated: true,
    direction_note: '缺少成交主动方，无法判断真实交易方向',
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
  ranked.sort(
    (left, right) =>
      right.severity - left.severity ||
      right.ratio - left.ratio ||
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
