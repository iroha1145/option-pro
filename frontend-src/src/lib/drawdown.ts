/**
 * 区间回撤测量 —— 纯计算层（KlineChart 回撤尺）
 * 口径：
 *  - wick：下跌段量 起点High → 终点Low；选段整体上涨（按收盘判向）时换 起点Low → 终点High，
 *    量极值仍符合图上直觉；单根 bar 一律量当根振幅 High → Low。
 *  - close：两端一律收盘价（与后端 _drawdown_from_high / drawdown_3m 的收盘口径可比）。
 * 锚点存 bar 时间戳（epoch ms）：usePolling 会静默刷新 bars，索引会漂移而时间戳不变；
 * 刷新后按时间戳重新解析，解析不到即判失效，绝不悄悄吸附到别的 bar。
 */

export type MeasureBasis = 'wick' | 'close';

/** 计算所需的最小 bar 形状（ChartBarEx 子集） */
export interface MeasureBar {
  t: string;
  o: number;
  h: number;
  l: number;
  c: number;
  quote_only?: boolean;
}

export interface RangeMeasure {
  startIdx: number;
  endIdx: number;
  startMs: number;
  endMs: number;
  startPrice: number;
  endPrice: number;
  /** endPrice − startPrice，负值即回撤 */
  changeAbs: number;
  /** (endPrice / startPrice − 1) × 100 */
  changePct: number;
  /** 回撤段回到起点价所需涨幅（正数）；上涨段为 null */
  recoveryPct: number | null;
  /** 含两端的 bar 数 */
  barCount: number;
  /** 覆盖的纽约时区自然日数（日线即交易日数；周线请用 barCount 表述） */
  sessionDays: number;
  isDrawdown: boolean;
}

const NY_DAY = new Intl.DateTimeFormat('en-CA', {
  timeZone: 'America/New_York',
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
});

export function barTimeMs(bar: MeasureBar): number {
  return Date.parse(bar.t);
}

/** 时间戳 → bar 索引；找不到（数据窗口滚动 / 标的切换）返回 -1 */
export function resolveAnchor(bars: MeasureBar[], ms: number): number {
  return bars.findIndex((b) => Date.parse(b.t) === ms);
}

/**
 * 距 idx 最近的可测 bar，跳过 quote_only（其 High/Low 为报价包络合成值，
 * 与图上半透明弱化的语义一致：弱化的 bar 不参与测量）。全为 quote_only 返回 -1。
 */
export function snapToMeasurableBar(bars: MeasureBar[], idx: number): number {
  if (!bars.length) return -1;
  const clamped = Math.max(0, Math.min(bars.length - 1, Math.round(idx)));
  if (!bars[clamped]?.quote_only) return clamped;
  for (let step = 1; step < bars.length; step++) {
    const left = clamped - step;
    const right = clamped + step;
    if (left >= 0 && !bars[left].quote_only) return left;
    if (right < bars.length && !bars[right].quote_only) return right;
  }
  return -1;
}

export function measureRange(
  bars: MeasureBar[],
  aMs: number,
  bMs: number,
  basis: MeasureBasis,
): RangeMeasure | null {
  const ai = resolveAnchor(bars, aMs);
  const bi = resolveAnchor(bars, bMs);
  if (ai < 0 || bi < 0) return null;
  // 点击顺序自由：按时间归一，早的为起点
  const startIdx = Math.min(ai, bi);
  const endIdx = Math.max(ai, bi);
  const start = bars[startIdx];
  const end = bars[endIdx];

  let startPrice: number;
  let endPrice: number;
  if (basis === 'close') {
    startPrice = start.c;
    endPrice = end.c;
  } else if (startIdx === endIdx) {
    startPrice = start.h;
    endPrice = end.l;
  } else if (end.c >= start.c) {
    startPrice = start.l;
    endPrice = end.h;
  } else {
    startPrice = start.h;
    endPrice = end.l;
  }
  if (!Number.isFinite(startPrice) || !Number.isFinite(endPrice) || startPrice <= 0 || endPrice <= 0) {
    return null;
  }

  const changePct = (endPrice / startPrice - 1) * 100;
  const days = new Set<string>();
  for (let i = startIdx; i <= endIdx; i++) {
    days.add(NY_DAY.format(new Date(bars[i].t)));
  }

  return {
    startIdx,
    endIdx,
    startMs: barTimeMs(start),
    endMs: barTimeMs(end),
    startPrice,
    endPrice,
    changeAbs: endPrice - startPrice,
    changePct,
    recoveryPct: changePct < 0 ? (startPrice / endPrice - 1) * 100 : null,
    barCount: endIdx - startIdx + 1,
    sessionDays: days.size,
    isDrawdown: changePct < 0,
  };
}
