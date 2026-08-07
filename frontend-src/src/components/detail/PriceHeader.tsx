/**
 * 个股整页 S0 头部（原 StockDrawerBody 抽屉头，抽屉撤除后由整页独占）
 * TickerLogo/名称/大价格 Data-XXL(count-up + tick-flash)/ChangeBadge/时段 chip/quote_as_of
 */
import { useEffect, useRef, useState } from 'react';
import { motion } from 'framer-motion';
import { marketApi } from '@/api/modules/market';
import { usePolling } from '@/hooks/usePolling';
import { useCountUp } from '@/hooks/useCountUp';
import { cn } from '@/lib/utils';
import { fmtCompact, fmtPrice, fmtTimeHHMMSS } from '@/lib/format';
import TickerLogo from '@/components/shared/TickerLogo';
import ChangeBadge from '@/components/shared/ChangeBadge';
import SessionLED from '@/components/shared/SessionLED';
import StrengthBar from '@/components/shared/StrengthBar';
import InfoHint from '@/components/shared/InfoHint';
import { SCORE_HINTS } from '@/lib/scoreHints';
import type { StockDetail } from '@/api/types';
import { t, t as __t } from '../../i18n/core.ts';

/** live 缺失数值字段（类型为 number 但运行时可为 null）如实显「—」 */
const isNum = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v);
const compactOr = (v: number | null | undefined): string => (isNum(v) ? fmtCompact(v) : '—');

export default function PriceHeader({ detail }: { detail: StockDetail }) {
  const { data: market } = usePolling(() => marketApi.status(), 60_000, []);
  const shown = useCountUp(detail.price);
  const prevPrice = useRef(detail.price);
  const [flash, setFlash] = useState<'up' | 'down' | null>(null);

  useEffect(() => {
    if (detail.price !== prevPrice.current) {
      const direction = detail.price > prevPrice.current ? 'up' : 'down';
      prevPrice.current = detail.price;
      const start = window.setTimeout(() => setFlash(direction), 0);
      const finish = window.setTimeout(() => setFlash(null), 600);
      return () => {
        window.clearTimeout(start);
        window.clearTimeout(finish);
      };
    }
  }, [detail.price]);

  return (
    <motion.header
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.48, ease: [0.16, 1, 0.3, 1] }}
    >
      <div className="flex flex-wrap items-center gap-3">
        <TickerLogo ticker={detail.ticker} size={40} />
        <div className="min-w-0">
          <p className="flex flex-wrap items-baseline gap-x-2.5">
            <span className="font-display text-[22px] leading-[28px] font-bold text-ink-900">{detail.ticker}</span>
            <span className="text-body-s text-ink-500">{detail.name}</span>
          </p>
          <div className="mt-1 flex flex-wrap items-center gap-2">
            <span className="rounded-xs border border-line-strong bg-card-warm px-1.5 py-px text-micro text-ink-500">
              {detail.sector}
            </span>
            {market && <SessionLED session={market.session} label={t('{label} · 延迟 15 分钟', { label: market.label })} />}
          </div>
        </div>
        <div className="ml-auto text-right">
          <p className="eyebrow">
            {__t('强度分')}
            <InfoHint hint={SCORE_HINTS.strengthComposite} side="bottom" align="end" size={12} className="ml-1" />
          </p>
          <StrengthBar score={detail.strengthScore} width={72} className="mt-1.5" />
        </div>
      </div>

      <div className="mt-4 flex flex-wrap items-end justify-between gap-x-6 gap-y-3">
        <div className="min-w-0 max-w-full flex flex-wrap items-end gap-x-3 gap-y-2">
          <p
            className={cn(
              'rounded-sm px-1 font-mono text-[clamp(30px,10vw,44px)] font-medium leading-none tracking-[-0.02em] text-ink-900 tnum',
              flash === 'up' && 'animate-tick-flash-up',
              flash === 'down' && 'animate-tick-flash-down',
            )}
          >
            ${fmtPrice(shown)}
          </p>
          <div className="flex items-center gap-2 pb-1.5">
            <ChangeBadge value={detail.changePct} />
            {isNum(detail.change) && (
              <span className={cn('font-mono text-data-m tnum', detail.change >= 0 ? 'text-up-700' : 'text-down-700')}>
                {detail.change >= 0 ? '+' : '−'}{fmtPrice(Math.abs(detail.change))}
              </span>
            )}
          </div>
        </div>
        <p className="pb-1.5 text-right font-mono text-micro text-ink-500 tnum">
          {__t('成交量')} {compactOr(detail.volume)} {__t('· 市值')} {isNum(detail.marketCap) ? `$${fmtCompact(detail.marketCap)}` : '—'}
        </p>
      </div>

      <p className="mt-2 text-micro text-ink-400">
        {__t('报价更新于')} <span className="font-mono tnum">{fmtTimeHHMMSS(new Date(detail.updatedAt))}</span>
        {__t(' · 延迟行情')}
      </p>
    </motion.header>
  );
}
