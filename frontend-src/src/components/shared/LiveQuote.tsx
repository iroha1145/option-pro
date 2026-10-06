import { t } from '../../i18n/core.ts';
import { useLiveQuote, useQuoteStatus } from '@/hooks/useLiveQuote';
import { useTickFlash } from '@/hooks/useTickFlash';
import { displayedQuoteLabel, fallbackQuoteLabel, liveQuoteChangePct, preferLiveQuote, visibleQuoteDate, type FallbackQuoteKind } from '@/lib/liveQuotes';
import { fmtPrice, fmtTimeHHMMSS } from '@/lib/format';
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
    ? fmtTimeHHMMSS(new Date(at))
    : null;
  const fullLabel = quote
    ? displayedQuoteLabel(quote, status, !usingFallback, fallbackAt, fallbackKind)
    : fallbackQuoteLabel(fallbackAt, fallbackKind);
  /* 2026-10-06 第二轮：列表里这行常折成三行（「暂无新成交 · 最后报价 2026-10-05」）。
     可见文字只留短标签和月-日（今年的日期省年份）；完整说明与带年份的日期在悬停提示里。 */
  const label = fullLabel === t('暂无新成交 · 最后报价') ? t('最后报价') : fullLabel;
  const shortDay = day && day.slice(0, 4) === String(new Date().getFullYear()) ? day.slice(5) : day;
  /* 年份已在「报价日期」里；只有说明文字被缩短时，才把原文放到提示开头。 */
  const detail = label !== fullLabel ? fullLabel : null;
  return (
    <span
      className={cn('text-micro font-normal text-ink-400', className)}
      title={[detail, stamp && t('报价时间 {time}（纽约）', { time: stamp }), day && t('报价日期 {date}', { date: day }), !usingFallback && quote?.source, !usingFallback && quote?.previous_close != null && quote.previous_close > 0 && t('昨收 ${price}', { price: fmtPrice(quote.previous_close) })].filter(Boolean).join(' · ')}
    >
      {label}
      {/* 日期整体换行，不在连字符处断开 */}
      {shortDay ? <span className="ml-1 whitespace-nowrap tnum">{shortDay}</span> : null}
    </span>
  );
}
/** 只渲染报价说明（价格另行排版时用，如选股表把说明放到价格下一行）。判断口径与 LivePrice 相同。 */
export function LiveQuoteLabel({ symbol, fallback, fallbackAt, fallbackKind = 'reference', className }: { symbol: string; fallback?: number | null; fallbackAt?: string | null; fallbackKind?: FallbackQuoteKind; className?: string }) {
  const normalizedSymbol = symbol.trim().toUpperCase();
  const quote = useLiveQuote(normalizedSymbol);
  const hasFallback = typeof fallback === 'number' && Number.isFinite(fallback) && fallback > 0;
  const useLive = preferLiveQuote(quote, hasFallback, fallbackAt);
  return <QuoteIndicator symbol={normalizedSymbol} usingFallback={!useLive && hasFallback} fallbackAt={fallbackAt} fallbackKind={fallbackKind} className={className} />;
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
