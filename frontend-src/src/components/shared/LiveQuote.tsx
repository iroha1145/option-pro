import { t } from '../../i18n/core.ts';
import { useLiveQuote, useQuoteStatus } from '@/hooks/useLiveQuote';
import { useTickFlash } from '@/hooks/useTickFlash';
import { displayedQuoteLabel, preferLiveQuote } from '@/lib/liveQuotes';
import { fmtPrice } from '@/lib/format';
import { cn } from '@/lib/utils';
import NumberTicker from './NumberTicker';
import ChangeBadge from './ChangeBadge';

export function QuoteIndicator({ symbol, className, usingFallback = false, fallbackAt }: { symbol: string; className?: string; usingFallback?: boolean; fallbackAt?: string | null }) {
  const status = useQuoteStatus();
  const quote = useLiveQuote(symbol);
  if (!quote) return null;
  const at = usingFallback ? fallbackAt : quote.trade_at;
  const stamp = at ? new Date(at).toLocaleTimeString('zh-CN', { hour12: false }) : null;
  return <span className={cn('text-[10px] font-normal text-ink-400', className)} title={[stamp && t('成交时间 {time}', { time: stamp }), !usingFallback && quote.source, !usingFallback && quote.previous_close != null && t('昨收 ${price}', { price: fmtPrice(quote.previous_close) })].filter(Boolean).join(' · ')}>{displayedQuoteLabel(quote, status, !usingFallback)}</span>;
}
const flashKey = () => 'price';
const flashValue = (value: number | null) => value;
export function LivePrice({ symbol, fallback, fallbackAt, prefix = '', className, indicator = true }: { symbol: string; fallback?: number | null; fallbackAt?: string | null; prefix?: string; className?: string; indicator?: boolean }) {
  const quote = useLiveQuote(symbol);
  const hasFallback = typeof fallback === 'number' && Number.isFinite(fallback) && fallback > 0;
  const useLive = preferLiveQuote(quote, hasFallback, fallbackAt);
  const price = useLive ? quote?.price : fallback;
  const flashes = useTickFlash([price ?? null], flashKey, flashValue);
  return <span data-quote-symbol={symbol} className={cn('inline-flex flex-wrap items-baseline gap-x-1.5', className)}><span className={cn('tick-flash rounded-xs', flashes.price === 'up' && 'tick-flash-up', flashes.price === 'down' && 'tick-flash-down')}><NumberTicker text={typeof price === 'number' && Number.isFinite(price) ? `${prefix}${fmtPrice(price)}` : '—'} /></span>{indicator && <QuoteIndicator symbol={symbol} usingFallback={!useLive && hasFallback} fallbackAt={fallbackAt} />}</span>;
}
export function LiveChange({ symbol, fallback, fallbackAt, ...props }: { symbol: string; fallback?: number | null; fallbackAt?: string | null; size?: 'sm' | 'md'; className?: string }) {
  const quote = useLiveQuote(symbol);
  return <ChangeBadge value={preferLiveQuote(quote, typeof fallback === 'number' && Number.isFinite(fallback), fallbackAt) ? quote?.change_pct : fallback} {...props} />;
}
