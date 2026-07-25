/**
 * Index Tape（design.md §7.1）
 * 36px 指数跑马灯 marquee · hover 暂停 · 涨跌 tick-flash · 右侧固定「延迟行情」毛玻璃标签
 */
import { memo, useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router';
import { marketApi } from '@/api/modules/market';
import { usePolling } from '@/hooks/usePolling';
import { fmtPct, fmtPrice } from '@/lib/format';
import { cn } from '@/lib/utils';
import type { IndexQuote } from '@/api/types';

function TapeItem({ q, flash, onOpen }: { q: IndexQuote; flash: 'up' | 'down' | null; onOpen: (code: string) => void }) {
  /* 平盘用中性色，不画成上涨（审计 P2-8 同一口径）。 */
  const tone = q.changePct > 0 ? 'up' : q.changePct < 0 ? 'down' : 'flat';
  return (
    <button
      type="button"
      onClick={() => onOpen(q.code)}
      title={`查看大盘强弱 · ${q.code}`}
      aria-label={`查看大盘强弱，${q.code} 最新价 ${fmtPrice(q.price)}，${
        tone === 'flat' ? '持平' : `涨跌 ${fmtPct(q.changePct)}`
      }`}
      className={cn(
        'inline-flex cursor-pointer items-baseline gap-2 rounded-xs px-1 transition-colors duration-150 hover:bg-brand-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500/30',
        flash === 'up' && 'animate-tick-flash-up',
        flash === 'down' && 'animate-tick-flash-down',
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

export default function IndexTape() {
  const { data } = usePolling(() => marketApi.indices(), 60_000);
  const [flashes, setFlashes] = useState<Record<string, 'up' | 'down'>>({});
  const prevRef = useRef<Record<string, number>>({});
  const navigate = useNavigate();
  const openMarket = (code: string) => navigate(`/market?index=${encodeURIComponent(code)}`);

  useEffect(() => {
    if (!data) return;
    const next: Record<string, 'up' | 'down'> = {};
    data.forEach((q) => {
      const prev = prevRef.current[q.code];
      if (prev !== undefined && prev !== q.price) next[q.code] = q.price > prev ? 'up' : 'down';
      prevRef.current[q.code] = q.price;
    });
    if (Object.keys(next).length) {
      setFlashes(next);
      const t = setTimeout(() => setFlashes({}), 700);
      return () => clearTimeout(t);
    }
  }, [data]);

  const items = data ?? [];

  return (
    <div className="marquee-track relative flex h-9 items-center overflow-hidden border-b border-line bg-paper-2/80">
      <div className="marquee-inner flex w-max animate-marquee items-center gap-8 whitespace-nowrap pl-4" aria-hidden={items.length === 0}>
        <TapeRow items={items} flashes={flashes} onOpen={openMarket} />
        <TapeRow items={items} flashes={flashes} onOpen={openMarket} />
      </div>
      <span className="glass absolute right-0 top-0 z-10 flex h-full items-center border-l border-line px-3 text-micro font-medium text-ink-400">
        延迟行情
      </span>
    </div>
  );
}
