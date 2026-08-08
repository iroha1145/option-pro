/**
 * Index Tape（design.md §7.1）
 * 36px 指数跑马灯 marquee · hover 暂停 · 涨跌 tick-flash · 右侧固定「延迟行情」毛玻璃标签
 */
import { memo } from 'react';
import { useNavigate } from 'react-router';
import { marketApi } from '@/api/modules/market';
import { usePolling } from '@/hooks/usePolling';
import { useTickFlash } from '@/hooks/useTickFlash';
import { fmtPct, fmtPrice } from '@/lib/format';
import { cn } from '@/lib/utils';
import type { IndexQuote } from '@/api/types';
import { t } from '../i18n/core.ts';

function TapeItem({ q, flash, onOpen }: { q: IndexQuote; flash: 'up' | 'down' | null; onOpen: (code: string) => void }) {
  /* 平盘用中性色，不画成上涨（审计 P2-8 同一口径）。 */
  const tone = q.changePct > 0 ? 'up' : q.changePct < 0 ? 'down' : 'flat';
  return (
    <button
      type="button"
      onClick={() => onOpen(q.code)}
      title={t('查看大盘强弱 · {code}', { code: q.code })}
      aria-label={
        tone === 'flat'
          ? t('查看大盘强弱，{code} 最新价 {price}，{flat}', { code: q.code, price: fmtPrice(q.price), flat: t('持平') })
          : t('查看大盘强弱，{code} 最新价 {price}，涨跌 {pct}', { code: q.code, price: fmtPrice(q.price), pct: fmtPct(q.changePct) })
      }
      className={cn(
        'tick-flash inline-flex cursor-pointer items-baseline gap-2 rounded-xs px-1 transition-colors duration-150 hover:bg-brand-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500/30',
        flash === 'up' && 'tick-flash-up',
        flash === 'down' && 'tick-flash-down',
      )}
    >
      <span className="font-mono text-caption font-semibold text-ink-800">{q.code}</span>
      <span className="font-mono text-caption text-ink-600 tnum">{fmtPrice(q.price)}</span>
      <span
        className={cn(
          'font-mono text-caption tnum',
          tone === 'up' ? 'text-up-700' : tone === 'down' ? 'text-down-700' : 'text-ink-500',
        )}
      >
        {tone === 'flat' ? '0.00%' : fmtPct(q.changePct)}
      </span>
      <span className="ml-2 text-[8px] text-ink-300" aria-hidden="true">◆</span>
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

export default function IndexTape() {
  const { data } = usePolling(() => marketApi.indices(), 60_000);
  /* 闪烁定时器由 useTickFlash 单独持有：旧写法把定时器当 effect cleanup，
     下一轮没有价格变化时状态就再也没人清除（审计 P2-5）。 */
  const flashes = useTickFlash(data, tapeKey, tapePrice);
  const navigate = useNavigate();
  const openMarket = (code: string) => navigate(`/market?index=${encodeURIComponent(code)}`);

  const items = data ?? [];

  return (
    <div className="marquee-track relative flex h-9 items-center overflow-hidden border-b border-line bg-paper-2/80">
      <div className="marquee-inner flex w-max animate-marquee items-center gap-8 whitespace-nowrap pl-4" aria-hidden={items.length === 0}>
        <TapeRow items={items} flashes={flashes} onOpen={openMarket} />
        {/* 第二套只为无缝滚动存在：不能让键盘与读屏软件把每个指数访问两遍
            （审计 P3-4）。aria-hidden 挡读屏，inert 挡 Tab 与点击。 */}
        <div className="contents" aria-hidden="true" inert>
          <TapeRow items={items} flashes={flashes} onOpen={openMarket} />
        </div>
      </div>
      <span className="glass absolute right-0 top-0 z-10 flex h-full items-center border-l border-line px-3 text-micro font-medium text-ink-400">
        {t('延迟行情')}
      </span>
    </div>
  );
}
