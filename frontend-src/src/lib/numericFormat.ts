/** Financial display helpers: missing/invalid numbers never become zero. */
const missing = '—';
function finite(n: unknown): n is number { return typeof n === 'number' && Number.isFinite(n); }
function precision(n: number): number { return finite(n) ? Math.max(0, Math.min(20, Math.trunc(n))) : 2; }

export function fmtPrice(n: number | null | undefined, digits = 2): string {
  if (!finite(n)) return missing;
  const places = precision(digits);
  return n.toLocaleString('en-US', { minimumFractionDigits: places, maximumFractionDigits: places });
}
export function fmtSigned(n: number | null | undefined, digits = 2): string {
  if (!finite(n)) return missing;
  return `${n >= 0 ? '+' : '−'}${fmtPrice(Math.abs(n), digits)}`;
}
export function fmtPct(n: number | null | undefined, digits = 2): string {
  if (!finite(n)) return missing;
  return `${n >= 0 ? '+' : '−'}${Math.abs(n).toFixed(precision(digits))}%`;
}
export function fmtCompact(n: number | null | undefined): string {
  if (!finite(n)) return missing;
  const magnitude = Math.abs(n);
  if (magnitude >= 1e12) return `${(n / 1e12).toFixed(2)}T`;
  if (magnitude >= 1e9) return `${(n / 1e9).toFixed(2)}B`;
  if (magnitude >= 1e6) return `${(n / 1e6).toFixed(1)}M`;
  if (magnitude >= 1e3) return `${(n / 1e3).toFixed(1)}K`;
  return String(Math.round(n));
}
