import type { StructuralOverlay } from './structuralOverlays.ts';
import type { PatternInkInput } from '../linePresentation.ts';
type Translate = (message: string, variables?: Record<string, string | number | null | undefined>) => string;
const identity: Translate = (s, v) => s.replace(/\{(\w+)\}/g, (m, k: string) => String(v?.[k] ?? m));
export function overlayTier(o: Pick<StructuralOverlay, 'evidence'>): PatternInkInput['tier'] {
  const tier = o.evidence.displayTier;
  return tier === 'primary' || tier === 'secondary' || tier === 'context' || tier === 'historical' ? tier : undefined;
}
/** The original role stays in the name. A break is not an automatic role reversal. */
export function semanticLabel(base: string | null | undefined, o: Pick<StructuralOverlay, 'status' | 'evidence'>, t: Translate = identity): string | undefined {
  if (!base) return undefined;
  if (o.status === 'broken_up') return t('原{label} · 突破已确认', { label: base });
  if (o.status === 'broken_down') return t('原{label} · 跌破已确认', { label: base });
  if (['invalidated', 'failed', 'expired'].includes(o.status)) return t('{label} · 历史结构', { label: base });
  if (o.evidence.visualState === 'breakout_pending') return t('{label} · 突破待确认', { label: base });
  if (o.evidence.visualState === 'breakdown_pending') return t('{label} · 跌破待确认', { label: base });
  const tier = overlayTier(o);
  if (tier === 'secondary' || tier === 'context') return t('{label} · 参考', { label: base });
  return base;
}
/** Each unresolved anchor suppresses the whole gap, not a guessed rectangle. */
export function gapAreas(o: Pick<StructuralOverlay, 'geometry' | 'status'>, indexOf: (key: string) => number, t: Translate = identity): object[] {
  const g = o.geometry;
  if (typeof g.startBarKey !== 'string' || typeof g.endBarKey !== 'string') return [];
  const start = indexOf(g.startBarKey), end = indexOf(g.endBarKey);
  if (start < 0 || end < start) return [];
  const raw = o.status === 'expired' ? [{ low: g.low, high: g.high }] : g.remainingIntervals;
  if (!Array.isArray(raw) || raw.length > 8) return [];
  const intervals = raw.flatMap(p => {
    if (!p || typeof p !== 'object') return [];
    const low = (p as Record<string, unknown>).low, high = (p as Record<string, unknown>).high;
    return typeof low === 'number' && typeof high === 'number' && Number.isFinite(low) && Number.isFinite(high) && low > 0 && high > low
      ? [{ low, high }] : [];
  });
  if (intervals.length !== raw.length) return [];
  const largest = intervals.reduce((winner, p, i) => p.high - p.low > intervals[winner].high - intervals[winner].low ? i : winner, 0);
  const historical = o.status === 'expired';
  return intervals.map((p, i) => [
    { xAxis: start, yAxis: p.low,
      itemStyle: { color: historical ? 'rgba(82,97,122,0.025)' : 'rgba(184,120,33,0.10)',
        borderColor: historical ? 'rgba(82,97,122,0.16)' : 'rgba(184,120,33,0.30)', borderWidth: 0.7, borderType: 'dashed' },
      label: { show: i === largest, position: 'insideTopLeft', fontSize: 10, color: '#866026',
        formatter: historical ? t('价格缺口 · 已回补') : t('价格缺口 · 未回补') } },
    { xAxis: end, yAxis: p.high },
  ]);
}
