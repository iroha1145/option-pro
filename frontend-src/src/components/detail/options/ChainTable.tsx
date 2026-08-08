/**
 * 期权链三带表（md 及以上）：CALLS ｜ 行权价 ｜ PUTS。
 * 每行升级为数据条行：量/持单元格铺「水位条」（全链最大值归一，call 侧 up-600
 * 系朝右生长、put 侧 down-600 系朝左生长，条向行权价轴心聚拢），数字叠在条上。
 * ATM 行 brand 高亮 + 左竖条 + 「现价」胶囊；异动行 warn-50 底 + AlertChip；
 * ITM 侧浅底（异动行让位给 warn 底，保证异动一眼可见）。
 */
import { motion } from 'framer-motion';
import { cn } from '@/lib/utils';
import { DUR_SECTION } from '@/lib/motion';
import { fmtCompact, fmtPrice } from '@/lib/format';
import { t } from '../../../i18n/core.ts';
import type { OptionChain, OptionChainRow } from '@/api/types';
import { midpoint } from '../optionAnalysis.ts';
import AlertChip from './AlertChip.tsx';
import {
  barShare,
  rowMeta,
  volOiBadge,
  type ChainTotals,
} from './chainMetrics.ts';

const EASE: [number, number, number, number] = [0.16, 1, 0.3, 1];

/** 缺失数值显「—」，不落回 0。 */
const dash = (value: number | null, render: (n: number) => string): string =>
  value === null ? '—' : render(value);

/** 量/持单元格：低透明度水位条 + 叠加数字。 */
function WaterCell({
  value,
  max,
  side,
  align,
  shaded,
  delay,
}: {
  value: number | null;
  max: number | null;
  side: 'call' | 'put';
  align: 'right' | 'left';
  shaded: boolean;
  delay: number;
}) {
  const share = barShare(value, max);
  return (
    <td
      className={cn(
        'relative px-2 py-1.5',
        align === 'right' ? 'text-right' : 'text-left',
        shaded && 'bg-paper-2',
      )}
    >
      {share > 0 && (
        <motion.span
          aria-hidden="true"
          className={cn(
            'absolute inset-y-0',
            side === 'call' ? 'right-0 bg-up-600/10' : 'left-0 bg-down-600/10',
          )}
          style={{
            width: `${share * 100}%`,
            transformOrigin: side === 'call' ? 'right' : 'left',
          }}
          initial={{ scaleX: 0 }}
          animate={{ scaleX: 1 }}
          transition={{ duration: DUR_SECTION, delay: delay + 0.06, ease: EASE }}
        />
      )}
      <span className="relative text-ink-700">{dash(value, fmtCompact)}</span>
    </td>
  );
}

export default function ChainTable({
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
    <table className="w-full min-w-[560px] border-collapse whitespace-nowrap font-mono text-micro tnum">
      <thead className="sticky top-0 z-10">
        <tr className="bg-card-warm font-sans text-micro">
          <th colSpan={3} className="px-2 pb-0.5 pt-2 text-right font-semibold text-up-700">
            {t('看涨 CALLS')}
          </th>
          <th
            rowSpan={2}
            className="px-2 py-2 text-center align-bottom font-medium text-ink-400"
          >
            {t('行权价')}
          </th>
          <th colSpan={3} className="px-2 pb-0.5 pt-2 text-left font-semibold text-down-700">
            {t('看跌 PUTS')}
          </th>
        </tr>
        <tr className="bg-card-warm font-sans text-[10px] text-ink-400">
          <th className="px-2 pb-1.5 text-right font-normal">{t('中价')}</th>
          <th className="px-2 pb-1.5 text-right font-normal">{t('量')}</th>
          <th className="px-2 pb-1.5 text-right font-normal">{t('持')}</th>
          <th className="px-2 pb-1.5 text-left font-normal">{t('持')}</th>
          <th className="px-2 pb-1.5 text-left font-normal">{t('量')}</th>
          <th className="px-2 pb-1.5 text-left font-normal">{t('中价')}</th>
        </tr>
      </thead>
      <tbody>
        {chain.rows.map((r: OptionChainRow, i: number) => {
          const m = rowMeta(r);
          const isAtm = r.strike === atmStrike;
          const callItm = chain.spot !== null && r.strike < chain.spot;
          const putItm = chain.spot !== null && r.strike > chain.spot;
          const alert = m.callAlert || m.putAlert;
          const delay = Math.min(i * 0.012, 0.18);
          const callBadge = volOiBadge(m.callVolOi);
          const putBadge = volOiBadge(m.putVolOi);
          const premiums = [m.callPremium, m.putPremium].filter(
            (value): value is number => value !== null,
          );
          return (
            <motion.tr
              key={`${exp}-${r.strike}`}
              initial={{ opacity: 0, y: 4 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.24, delay, ease: EASE }}
              ref={isAtm ? setAtmRef : undefined}
              className={cn(
                'border-t border-line align-middle transition-colors duration-fast',
                isAtm ? 'bg-brand-50' : alert ? 'bg-warn-50' : 'hover:bg-paper-2',
              )}
              title={
                alert
                  ? `${t('成交异动 {badge}', { badge: callBadge ?? putBadge ?? '' })}${
                      premiums.length > 0
                        ? t(' · 权利金流约 ${amount}（估算）', { amount: fmtCompact(Math.max(...premiums)) })
                        : t(' · 权利金不可估算（缺买卖价）')
                    }`
                  : undefined
              }
            >
              {/* CALLS 侧：中价 ｜ 量 ｜ 持（条朝右、向行权价轴心聚拢） */}
              <td className={cn('px-2 py-1.5 text-right', callItm && !alert && 'bg-paper-2')}>
                <div className="flex flex-col items-end gap-0.5">
                  <span className="text-ink-800">
                    {dash(midpoint(r.callBid, r.callAsk), (n) => fmtPrice(n))}
                  </span>
                  {m.callAlert && (
                    <AlertChip state={m.callVolOi} premium={m.callPremium} align="end" />
                  )}
                </div>
              </td>
              <WaterCell
                value={r.callVol}
                max={totals.maxVol}
                side="call"
                align="right"
                shaded={callItm && !alert}
                delay={delay}
              />
              <WaterCell
                value={r.callOi}
                max={totals.maxOi}
                side="call"
                align="right"
                shaded={callItm && !alert}
                delay={delay}
              />
              {/* 行权价 */}
              <td
                className={cn(
                  'relative px-2 py-1.5 text-center',
                  isAtm ? 'font-semibold text-brand-700' : 'text-ink-600',
                )}
              >
                {isAtm && (
                  <span
                    className="absolute inset-y-0 left-0 w-0.5 bg-brand-600"
                    aria-hidden="true"
                  />
                )}
                <span className="inline-flex items-center gap-1.5">
                  {fmtPrice(r.strike, r.strike >= 100 ? 0 : 2)}
                  {isAtm && (
                    <span className="rounded-pill border border-brand-100 bg-card px-1.5 py-px font-sans text-[10px] font-medium text-brand-700">
                      {t('现价')}
                    </span>
                  )}
                </span>
              </td>
              {/* PUTS 侧：持 ｜ 量 ｜ 中价（条朝左、向行权价轴心聚拢） */}
              <WaterCell
                value={r.putOi}
                max={totals.maxOi}
                side="put"
                align="left"
                shaded={putItm && !alert}
                delay={delay}
              />
              <WaterCell
                value={r.putVol}
                max={totals.maxVol}
                side="put"
                align="left"
                shaded={putItm && !alert}
                delay={delay}
              />
              <td className={cn('px-2 py-1.5 text-left', putItm && !alert && 'bg-paper-2')}>
                <div className="flex flex-col items-start gap-0.5">
                  <span className="text-ink-800">
                    {dash(midpoint(r.putBid, r.putAsk), (n) => fmtPrice(n))}
                  </span>
                  {m.putAlert && (
                    <AlertChip state={m.putVolOi} premium={m.putPremium} align="start" />
                  )}
                </div>
              </td>
            </motion.tr>
          );
        })}
      </tbody>
    </table>
  );
}
