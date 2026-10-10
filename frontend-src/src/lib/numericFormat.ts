/** Financial display helpers: missing/invalid numbers never become zero. */
const missing = '—';
function finite(n: unknown): n is number { return typeof n === 'number' && Number.isFinite(n); }
function precision(n: number): number { return finite(n) ? Math.max(0, Math.min(20, Math.trunc(n))) : 2; }

export function fmtPrice(n: number | null | undefined, digits = 2): string {
  if (!finite(n)) return missing;
  const places = precision(digits);
  return n.toLocaleString('en-US', { minimumFractionDigits: places, maximumFractionDigits: places });
}
export function fmtChartPrice(price: number): string {
  return price.toLocaleString('en-US', { maximumFractionDigits: price < 1 ? 4 : 2 });
}
export function fmtSigned(n: number | null | undefined, digits = 2): string {
  if (!finite(n)) return missing;
  return `${n >= 0 ? '+' : '−'}${fmtPrice(Math.abs(n), digits)}`;
}
export function fmtPct(n: number | null | undefined, digits = 2): string {
  if (!finite(n)) return missing;
  const places = precision(digits);
  const body = Math.abs(n).toFixed(places);
  // 先四舍五入再取符号：-0.004 在两位小数下是 0.00，不能写成 −0.00%。
  const sign = n < 0 && Number(body) !== 0 ? '−' : '+';
  return `${sign}${body}%`;
}
export function fmtCompact(n: number | null | undefined): string {
  if (!finite(n)) return missing;
  const sign = n < 0 ? '-' : '';
  const magnitude = Math.abs(n);
  const tiers = [
    [1e12, 2, 'T'],
    [1e9, 2, 'B'],
    [1e6, 1, 'M'],
    [1e3, 1, 'K'],
  ] as const;
  for (let i = 0; i < tiers.length; i += 1) {
    const [divisor, digits, suffix] = tiers[i];
    if (magnitude < divisor) continue;
    const body = (magnitude / divisor).toFixed(digits);
    // 999950 → 1000.0K 会越过下一档。T 以上没有更大单位，保留 1000.00T。
    if (Number(body) >= 1000 && i > 0) {
      const [nextDivisor, nextDigits, nextSuffix] = tiers[i - 1];
      return `${sign}${(magnitude / nextDivisor).toFixed(nextDigits)}${nextSuffix}`;
    }
    return `${sign}${body}${suffix}`;
  }
  return String(Math.round(n));
}
