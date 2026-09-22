/** Display times in the exchange timezone without changing bar identities or indices. */
const EXCHANGE_CLOCK = new Intl.DateTimeFormat('en-CA', {
  timeZone: 'America/New_York',
  year: 'numeric', month: '2-digit', day: '2-digit',
  hour: '2-digit', minute: '2-digit', hourCycle: 'h23',
});

export function formatChartTime(value: string, range: string, style: 'axis' | 'detail' = 'detail'): string {
  const daily = range === '1d' || range === '1w';
  // A date-only payload is already a trading date, not UTC midnight to convert.
  if (/^\d{4}-\d{2}-\d{2}$/.test(value)) {
    const date = new Date(`${value}T00:00:00Z`);
    if (!daily || !Number.isFinite(date.getTime()) || date.toISOString().slice(0, 10) !== value) return '—';
    return style === 'axis' ? value.slice(5) : value;
  }
  if (!/(?:Z|[+-]\d{2}:\d{2})$/i.test(value)) return '—';
  const date = new Date(value);
  if (!Number.isFinite(date.getTime())) return '—';
  const parts = Object.fromEntries(EXCHANGE_CLOCK.formatToParts(date).map((part) => [part.type, part.value]));
  const day = `${parts.year}-${parts.month}-${parts.day}`;
  const label = style === 'axis' ? day.slice(5) : day;
  if (daily) return label;
  return `${label} ${parts.hour}:${parts.minute}${style === 'detail' ? ' ET' : ''}`;
}

export function fmtAxisLabel(iso: string, range: string): string {
  return formatChartTime(iso, range, 'axis');
}

export function barTooltipTitle(iso: string, range: string): string {
  return formatChartTime(iso, range);
}

/** 末根 K 线自身的时间；日/周只到日期，分钟带时刻。 */
export function lastBarText(data: { bars: { t: string }[]; last_bar_at?: string | null }, range: string): string {
  const iso = data.bars[data.bars.length - 1]?.t ?? data.last_bar_at;
  return iso ? formatChartTime(iso, range) : '—';
}
