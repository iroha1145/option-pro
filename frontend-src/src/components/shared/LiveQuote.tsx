import { t } from '../../i18n/core.ts';
import { useLiveQuote, useQuoteStatus } from '@/hooks/useLiveQuote';
import { useTickFlash } from '@/hooks/useTickFlash';
import { displayedQuoteLabel, fallbackQuoteLabel, liveQuoteChangePct, preferLiveQuote, visibleQuoteDate, type FallbackQuoteKind } from '@/lib/liveQuotes';
import { fmtPrice } from '@/lib/format';
import { cn } from '@/lib/utils';
import NumberTicker from './NumberTicker';
import ChangeBadge from './ChangeBadge';

export function QuoteIndicator({ symbol, className, usingFallback = false, fallbackAt, fallbackKind = 'reference' }: { symbol: string; className?: string; usingFallback?: boolean; fallbackAt?: string | null; fallbackKind?: FallbackQuoteKind }) {
  const status = useQuoteStatus();
  const quote = useLiveQuote(symbol);
  if (!quote && !usingFallback) return null;
  const at = usingFallback ? fallbackAt : preferLiveQuote(quote, false) ? quote?.trade_at : null;
  const day = visibleQuoteDate(at);
  const stamp = day && at && !/^\d{4}-\d{2}-\d{2}$/.test(at.trim())
    ? new Date(at).toLocaleTimeString('zh-CN', { hour12: false, timeZone: 'America/New_York' })
    : null;
  const label = quote
    ? displayedQuoteLabel(quote, status, !usingFallback, fallbackAt, fallbackKind)
    : fallbackQuoteLabel(fallbackAt, fallbackKind);
  return (
    <span
      className={cn('text-[10px] font-normal text-ink-400', className)}
      title={[stamp && t('报价时间 {time}（纽约）', { time: stamp }), day && t('报价日期 {date}', { date: day }), !usingFallback && quote?.source, !usingFallback && quote?.previous_close != null && quote.previous_close > 0 && t('昨收 ${price}', { price: fmtPrice(quote.previous_close) })].filter(Boolean).join(' · ')}
    >
      {label}
      {day ? <span className="ml-1 font-mono tnum">{day}</span> : null}
    </span>
  );
}
const flashKey = () => 'price';
const flashValue = (value: number | null) => value;
export function LivePrice({ symbol, fallback, fallbackAt, fallbackKind = 'reference', prefix = '', className, indicator = true }: { symbol: string; fallback?: number | null; fallbackAt?: string | null; fallbackKind?: FallbackQuoteKind; prefix?: string; className?: string; indicator?: boolean }) {
  const normalizedSymbol = symbol.trim().toUpperCase();
  return <LivePriceValue key={normalizedSymbol} symbol={normalizedSymbol} fallback={fallback} fallbackAt={fallbackAt} fallbackKind={fallbackKind} prefix={prefix} className={className} indicator={indicator} />;
}

function LivePriceValue({ symbol, fallback, fallbackAt, fallbackKind, prefix, className, indicator }: Parameters<typeof LivePrice>[0]) {
  const quote = useLiveQuote(symbol);
  const hasFallback = typeof fallback === 'number' && Number.isFinite(fallback) && fallback > 0;
  const useLive = preferLiveQuote(quote, hasFallback, fallbackAt);
  const price = useLive ? quote?.price : hasFallback ? fallback : null;
  const flashes = useTickFlash([price ?? null], flashKey, flashValue);
  return <span data-quote-symbol={symbol} className={cn('inline-flex flex-wrap items-baseline gap-x-1.5', className)}><span className={cn('tick-flash rounded-xs', flashes.price === 'up' && 'tick-flash-up', flashes.price === 'down' && 'tick-flash-down')}><NumberTicker text={typeof price === 'number' && Number.isFinite(price) ? `${prefix}${fmtPrice(price)}` : '—'} /></span>{indicator && <QuoteIndicator symbol={symbol} usingFallback={!useLive && hasFallback} fallbackAt={fallbackAt} fallbackKind={fallbackKind} />}</span>;
}
/** Outer periodic flashes stay off while the visible price is already live. */
export function PeriodicPriceFlash({
  symbol, fallbackAt, flash, className, children,
}: {
  symbol: string;
  fallbackAt?: string | null;
  flash?: 'up' | 'down' | null;
  className?: string;
  children: React.ReactNode;
}) {
  const quote = useLiveQuote(symbol);
  const live = preferLiveQuote(quote, true, fallbackAt);
  return (
    <span className={cn(className, !live && flash === 'up' && 'tick-flash-up', !live && flash === 'down' && 'tick-flash-down')}>
      {children}
    </span>
  );
}
export function LiveChange({ symbol, fallback, fallbackPrice, fallbackAt, ...props }: { symbol: string; fallback?: number | null; fallbackPrice?: number | null; fallbackAt?: string | null; size?: 'sm' | 'md'; className?: string }) {
  const quote = useLiveQuote(symbol);
  const hasFallback = typeof fallback === 'number' && Number.isFinite(fallback);
  const hasPriceFallback = fallbackPrice === undefined ? hasFallback : typeof fallbackPrice === 'number' && Number.isFinite(fallbackPrice) && fallbackPrice > 0;
  const useLive = preferLiveQuote(quote, hasPriceFallback, fallbackAt);
  if (useLive) {
    return <ChangeBadge value={liveQuoteChangePct(quote)} {...props} />;
  }
  return <ChangeBadge value={hasFallback ? fallback : null} {...props} />;
}
