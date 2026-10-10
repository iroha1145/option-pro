/**
 * Index Tape（design.md §7.1）
 * 36px 指数跑马灯 marquee · hover 与键盘焦点暂停 · 涨跌 tick-flash · 右侧固定「延迟行情」毛玻璃标签
 */
import { memo, useCallback, useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router';
import { marketApi } from '@/api/modules/market';
import { usePolling } from '@/hooks/usePolling';
import { useLiveQuote, useQuoteStatus, useQuoteSymbols } from '@/hooks/useLiveQuote';
import { MARKET_FUNDS } from '@/lib/liveQuotes';
import { LivePrice, LiveChange } from '@/components/shared/LiveQuote';
import SameWidth from '@/components/shared/SameWidth';
import { useTickFlash } from '@/hooks/useTickFlash';
import { usePrefersReducedMotion } from '@/hooks/usePrefersReducedMotion';
import { fmtPct, fmtPrice } from '@/lib/format';
import { marqueeCopies, marqueeTimeAt } from '@/lib/marquee';
import { cn } from '@/lib/utils';
import type { IndexQuote } from '@/api/types';
import { t } from '../i18n/core.ts';

function TapeItem({ q, flash, onOpen }: { q: IndexQuote; flash: 'up' | 'down' | null; onOpen: (code: string) => void }) {
  /* 平盘用中性色，不画成上涨（审计 P2-8 同一口径）。 */
  const pctText = q.changePct === null ? null : fmtPct(q.changePct);
  const tone = q.changePct === null ? 'unknown' : q.changePct > 0 ? 'up' : q.changePct < 0 && pctText !== '+0.00%' ? 'down' : 'flat';
  return (
    <button
      type="button"
      onClick={() => onOpen(q.code)}
      title={t('查看美股概况 · {code}', { code: q.code })}
      aria-label={
        tone === 'flat' || tone === 'unknown'
          ? t('查看美股概况，{code} 最新价 {price}，{flat}', { code: q.code, price: fmtPrice(q.price), flat: tone === 'unknown' ? t('涨跌数据缺失') : t('持平') })
          : t('查看美股概况，{code} 最新价 {price}，涨跌 {pct}', { code: q.code, price: fmtPrice(q.price), pct: fmtPct(q.changePct) })
      }
      className={cn(
        'tick-flash inline-flex cursor-pointer items-center gap-2 rounded-xs px-1 transition-colors duration-fast hover:bg-paper-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-600',
        flash === 'up' && 'tick-flash-up',
        flash === 'down' && 'tick-flash-down',
      )}
    >
      <span className="tnum text-caption font-medium text-ink-800">{q.code}</span>
      <span className="text-caption text-ink-600 tnum">{fmtPrice(q.price)}</span>
      <span
        className={cn(
          ' text-caption tnum',
          tone === 'up' ? 'text-up-700' : tone === 'down' ? 'text-down-700' : 'text-ink-500',
        )}
      >
        {tone === 'flat' ? '0.00%' : pctText ?? '—'}
      </span>
    </button>
  );
}

const TapeRow = memo(function TapeRow({ items, flashes, onOpen }: { items: IndexQuote[]; flashes: Record<string, 'up' | 'down'>; onOpen: (code: string) => void }) {
  return (
    <>
      {items.map((q) => (
        <TapeItem key={q.code} q={q} flash={flashes[q.code] ?? null} onOpen={onOpen} />
      ))}
    </>
  );
});

const tapeKey = (q: IndexQuote) => q.code;
const tapePrice = (q: IndexQuote) => q.price;

const FUND_LABELS: Record<string, string> = { SPY: t('标普500基金'), QQQ: t('纳斯达克100基金'), DIA: t('道琼斯基金'), IWM: t('罗素2000基金') };
const FUND_PRICE_CLASS = 'tnum text-caption text-ink-600 [@media(pointer:coarse)]:items-center [@media(pointer:coarse)]:[&>.tick-flash]:inline-flex';
const FUND_CHANGE_CLASS = '[@media(pointer:coarse)]:text-caption';

/* 报价到达前后芯片等宽：隐形样例用等宽数字排出比常见读数略宽的一格（四位整数的价格、两位整数的涨跌），
   实际读数叠在同一格里，「—」换成价格时后面的芯片和副本都不动。样例是纯文字，不带滚动数字的十行字形。 */
const FUND_PRICE_SAMPLE = <span className={FUND_PRICE_CLASS}>$0000.00</span>;
const FUND_CHANGE_SAMPLE = <span className={cn('change-badge inline-flex items-center gap-0.5 text-[12px] leading-[16px] tnum', FUND_CHANGE_CLASS)}><span className="size-3" />−10.01%</span>;

function FundTapeItem({ symbol, onOpen }: { symbol: string; onOpen: () => void }) {
  const quote = useLiveQuote(symbol);
  return <button type="button" onClick={onOpen} title={t('{fund} · 美元价格', { fund: FUND_LABELS[symbol] })} className="inline-flex items-center gap-2 rounded-xs px-1 hover:bg-paper-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-600">
    <span className="text-caption font-medium text-ink-800">{FUND_LABELS[symbol]}</span>
    <span className="tnum text-micro text-ink-400 [@media(pointer:coarse)]:text-caption">{symbol}</span>
    {/* 触屏用同一字阶；价格的行内盒会给滚动数字留下下降空间，改成弹性盒后按可见数字居中。 */}
    <SameWidth samples={[FUND_PRICE_SAMPLE]}>
      <LivePrice symbol={symbol} prefix="$" indicator={false} className={FUND_PRICE_CLASS} />
    </SameWidth>
    <SameWidth samples={[FUND_CHANGE_SAMPLE]}>
      <LiveChange symbol={symbol} fallback={quote?.change_pct} size="sm" className={FUND_CHANGE_CLASS} />
    </SameWidth>
  </button>;
}

export default function IndexTape() {
  const quoteStatus = useQuoteStatus();
  const useFunds = quoteStatus.enabled && quoteStatus.configured && quoteStatus.allowed !== false;
  useQuoteSymbols(MARKET_FUNDS);
  const { data } = usePolling(() => marketApi.indices(), 60_000, [], { enabled: !useFunds });
  /* 闪烁定时器由 useTickFlash 单独持有：旧写法把定时器当 effect cleanup，
     下一轮没有价格变化时状态就再也没人清除（审计 P2-5）。 */
  const flashes = useTickFlash(data, tapeKey, tapePrice);
  const navigate = useNavigate();
  // 稳定引用，TapeRow 的 memo 才能在价格闪烁以外的重渲染里跳过各副本。
  const openMarket = useCallback((code: string) => navigate(`/market?index=${encodeURIComponent(code)}`), [navigate]);

  const items = data ?? [];
  const innerRef = useRef<HTMLDivElement>(null);
  const copyRef = useRef<HTMLDivElement>(null);
  const [copies, setCopies] = useState(2);
  const reducedMotion = usePrefersReducedMotion();

  /* 键盘焦点进来时露出第一套里的按钮。动画模式按位置停住；减少动态时没有动画，
     直接横向卷动轨道。标签在焦点停留期间隐藏，避免盖住较长的基金行情。 */
  const revealKeyboardFocus = useCallback((target: EventTarget | null) => {
    const inner = innerRef.current;
    const track = inner?.parentElement;
    const copy = copyRef.current;
    if (!inner || !track || !copy || !(target instanceof HTMLElement) || !copy.contains(target) || !target.matches(':focus-visible')) return;
    const animation = inner.getAnimations().find(item => item instanceof CSSAnimation && item.animationName === 'marquee');
    if (!animation) {
      track.scrollTo({ left: target.offsetLeft, behavior: 'instant' });
      return;
    }
    track.scrollTo({ left: 0, behavior: 'instant' });
    animation.currentTime = marqueeTimeAt(target.offsetLeft, copy.offsetWidth, Number(animation.effect?.getComputedTiming().duration));
  }, []);

  useEffect(() => {
    const copy = copyRef.current;
    const track = innerRef.current?.parentElement;
    if (!copy || !track || typeof ResizeObserver === 'undefined') return;
    // 没有内容时单套只剩尾部间距，按 0 宽处理，免得铺出上限份数的空副本。
    const measure = () => {
      setCopies(marqueeCopies(track.clientWidth, copy.childElementCount > 0 ? copy.offsetWidth : 0));
      revealKeyboardFocus(document.activeElement);
    };
    const observer = new ResizeObserver(measure);
    observer.observe(track);
    observer.observe(copy);
    return () => observer.disconnect();
  }, [revealKeyboardFocus]);

  useEffect(() => {
    // 恢复动画前清除手动滚动偏移，否则固定标签也会被卷出轨道。
    const track = innerRef.current?.parentElement;
    track?.scrollTo({ left: 0, behavior: 'instant' });
    revealKeyboardFocus(document.activeElement);
  }, [reducedMotion, revealKeyboardFocus]);

  const tapeLabels = [t('基金行情 · 美元'), t('行情连接中'), t('延迟行情')];
  const label = useFunds ? (quoteStatus.connected ? tapeLabels[0] : tapeLabels[1]) : tapeLabels[2];

  const row = useFunds
    ? MARKET_FUNDS.map(symbol => <FundTapeItem key={symbol} symbol={symbol} onOpen={() => navigate(`/stock/${symbol}`)} />)
    : <TapeRow items={items} flashes={flashes} onOpen={openMarket} />;

  return (
    <div className="marquee-track no-scrollbar relative flex h-9 items-center overflow-hidden border-b border-line bg-paper-2/80 pl-4" onFocus={(event) => revealKeyboardFocus(event.target)}>
      <div ref={innerRef} className="marquee-inner relative flex w-max shrink-0 animate-marquee items-center">
        <div ref={copyRef} className="flex items-center gap-8 whitespace-nowrap pr-8" aria-hidden={!useFunds && items.length === 0}>
          {row}
        </div>
        {/* 其余副本只为无缝滚动存在：不能让键盘与读屏软件把每个指数访问多遍
            （审计 P3-4）。aria-hidden 挡读屏，inert 挡 Tab 与点击。副本依次绝对定位在
            第一套之后，动画每轮正好平移一套的宽度（含尾部间距），接缝处不跳。 */}
        {Array.from({ length: copies - 1 }, (_, index) => (
          <div key={index} className="marquee-echo absolute inset-y-0 flex items-center gap-8 whitespace-nowrap pr-8" style={{ left: `${(index + 1) * 100}%` }} aria-hidden="true" inert>
            {row}
          </div>
        ))}
      </div>
      <span className="marquee-label absolute inset-y-0 right-0 z-10 flex items-stretch">
        <span className="pointer-events-none w-8 bg-gradient-to-r from-transparent to-paper-2" aria-hidden="true" />
        {/* 不用 .glass：底色已是 95% 不透明，磨砂看不出来，却要随下面一直滚动的跑马灯逐帧重算模糊。
            三种说明叠在同一格、宽度取最长的一种，连接状态变化时标签左缘不动。 */}
        <span className="grid items-center border-l border-line bg-paper-2/95 px-3 text-micro text-ink-400">
          {tapeLabels.map((text) => (
            <span key={text} className={cn('col-start-1 row-start-1', text !== label && 'invisible')} aria-hidden={text !== label || undefined}>{text}</span>
          ))}
        </span>
      </span>
    </div>
  );
}
