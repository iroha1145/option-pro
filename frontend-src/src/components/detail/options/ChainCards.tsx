/**
 * 期权链移动竖屏版（md 以下）：每个行权价一张小卡，替代横向滚动表格。
 * 卡内 CALL / PUT 左右两栏：中价 + 量/持（数值 + 归一小条，与桌面同一套
 * maxVol/maxOi 基准，竖屏一眼可比两侧量级）；异动卡 warn 边/底 + AlertChip，
 * ATM 卡 brand 边/底 + 「现价」胶囊。与桌面表共用同一个滚动容器与 ATM 居中。
 */
import { motion } from 'framer-motion';
import { cn } from '@/lib/utils';
import { fmtCompact, fmtPrice } from '@/lib/format';
import { t } from '../../../i18n/core.ts';
import type { OptionChain, OptionChainRow } from '@/api/types';
import { midpoint } from '../optionAnalysis.ts';
import AlertChip from './AlertChip.tsx';
import {
  barShare,
  rowMeta,
  type ChainTotals,
  type RowMeta,
} from './chainMetrics.ts';

const EASE: [number, number, number, number] = [0.16, 1, 0.3, 1];

/** 缺失数值显「—」，不落回 0。 */
const dash = (value: number | null, render: (n: number) => string): string =>
  value === null ? '—' : render(value);

/** 量/持一行：标签 + 数值 + 全链归一小条。 */
function MiniBar({
  label,
  value,
  max,
  side,
  delay,
}: {
  label: string;
  value: number | null;
  max: number | null;
  side: 'call' | 'put';
  delay: number;
}) {
  const share = barShare(value, max);
  return (
    <div>
      <div className="flex items-baseline justify-between gap-2 text-micro">
        <span className="font-sans text-ink-400">{label}</span>
        <span className="font-mono text-ink-700 tnum">{dash(value, fmtCompact)}</span>
      </div>
      <div className="mt-0.5 h-1 overflow-hidden rounded-pill bg-line" aria-hidden="true">
        {share > 0 && (
          <motion.div
            className={cn(
              'h-full rounded-pill',
              side === 'call' ? 'bg-up-600/60' : 'bg-down-600/60',
            )}
            style={{ transformOrigin: side === 'call' ? 'left' : 'right' }}
            initial={{ scaleX: 0 }}
            animate={{ scaleX: share }}
            transition={{ duration: 0.5, delay: delay + 0.06, ease: EASE }}
          />
        )}
      </div>
    </div>
  );
}

function SideBlock({
  side,
  row,
  meta,
  totals,
  shaded,
  delay,
}: {
  side: 'call' | 'put';
  row: OptionChainRow;
  meta: RowMeta;
  totals: ChainTotals;
  shaded: boolean;
  delay: number;
}) {
  const isCall = side === 'call';
  const vol = isCall ? row.callVol : row.putVol;
  const oi = isCall ? row.callOi : row.putOi;
  const mid = midpoint(isCall ? row.callBid : row.putBid, isCall ? row.callAsk : row.putAsk);
  const alerting = isCall ? meta.callAlert : meta.putAlert;
  return (
    <div
      className={cn(
        'rounded-md border border-line px-2.5 py-2',
        shaded ? 'bg-paper-2' : 'bg-card/80',
      )}
    >
      <p
        className={cn(
          'font-sans text-[10px] font-semibold tracking-wide',
          isCall ? 'text-up-700' : 'text-down-700',
        )}
      >
        {isCall ? t('CALLS') : t('PUTS')}
      </p>
      <p className="mt-1 font-mono text-body-s font-medium text-ink-800 tnum">
        {dash(mid, (n) => fmtPrice(n))}
      </p>
      <div className="mt-1.5 space-y-1.5">
        <MiniBar label={t('量')} value={vol} max={totals.maxVol} side={side} delay={delay} />
        <MiniBar label={t('持')} value={oi} max={totals.maxOi} side={side} delay={delay} />
      </div>
      {alerting && (
        <div className="mt-1.5">
          <AlertChip
            state={isCall ? meta.callVolOi : meta.putVolOi}
            premium={isCall ? meta.callPremium : meta.putPremium}
          />
        </div>
      )}
    </div>
  );
}

export default function ChainCards({
  chain,
  totals,
  atmStrike,
  exp,
  setAtmRef,
}: {
  chain: OptionChain;
  totals: ChainTotals;
  atmStrike: number | null;
  exp: string;
  setAtmRef: (el: HTMLElement | null) => void;
}) {
  return (
    <ol className="space-y-2 p-2">
      {chain.rows.map((r, i) => {
        const m = rowMeta(r);
        const isAtm = r.strike === atmStrike;
        const alert = m.callAlert || m.putAlert;
        const delay = Math.min(i * 0.012, 0.18);
        return (
          <motion.li
            key={`${exp}-${r.strike}`}
            ref={isAtm ? setAtmRef : undefined}
            initial={{ opacity: 0, y: 4 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.24, delay, ease: EASE }}
            className={cn(
              'rounded-lg border p-2.5',
              isAtm
                ? 'border-brand-600/40 bg-brand-50'
                : alert
                  ? 'border-warn-600/40 bg-warn-50'
                  : 'border-line bg-card',
            )}
          >
            <div className="flex items-center justify-between gap-2">
              <span
                className={cn(
                  'font-mono text-body-s tnum',
                  isAtm ? 'font-semibold text-brand-700' : 'font-medium text-ink-800',
                )}
              >
                {fmtPrice(r.strike, r.strike >= 100 ? 0 : 2)}
              </span>
              {isAtm && (
                <span className="rounded-pill border border-brand-100 bg-card px-1.5 py-px text-[10px] font-medium text-brand-700">
                  {t('现价')}
                </span>
              )}
            </div>
            <div className="mt-2 grid grid-cols-2 gap-2">
              <SideBlock
                side="call"
                row={r}
                meta={m}
                totals={totals}
                shaded={chain.spot !== null && r.strike < chain.spot && !alert}
                delay={delay}
              />
              <SideBlock
                side="put"
                row={r}
                meta={m}
                totals={totals}
                shaded={chain.spot !== null && r.strike > chain.spot && !alert}
                delay={delay}
              />
            </div>
          </motion.li>
        );
      })}
    </ol>
  );
}
