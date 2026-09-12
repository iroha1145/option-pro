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
