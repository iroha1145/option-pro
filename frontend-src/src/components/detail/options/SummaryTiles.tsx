/**
 * 期权链顶部摘要条：总成交量 / 总持仓量 / 估算权利金流 / 异动合约数。
 * 全部由当前链派生（chainMetrics）；缺失侧显「—」，不落 0。
 * 切到期日换 key 重挂，4 块砖 stagger 入场（总时长 ≈350ms）。
 */
import type { ReactNode } from 'react';
import { motion } from 'framer-motion';
import Icon from '@/components/icons';
import { fmtCompact } from '@/lib/format';
import { t } from '../../../i18n/core.ts';
import {
  cpRatio,
  sumSides,
  type ChainTotals,
} from './chainMetrics.ts';

const EASE: [number, number, number, number] = [0.16, 1, 0.3, 1];

function fmtRatio(value: number | null): string {
  if (value === null) return '—';
  if (!Number.isFinite(value)) return '∞';
  return value.toFixed(2);
}

/** Call/Put 双色占比条（up-600 系 vs down-600 系）。
 *  任一侧缺失就只画空轨道：把缺失当 0 会捏造出「全是另一侧」的占比
 *  （GPT-5.6-Pro 审计：摘要条不得破坏逐行「缺失显 —」的纪律）。 */
function SplitBar({
  call,
  put,
  delay,
}: {
  call: number | null;
  put: number | null;
  delay: number;
}) {
  if (call === null || put === null || call + put <= 0) {
    return <div className="mt-2 h-1 rounded-pill bg-line" aria-hidden="true" />;
  }
  const total = call + put;
  return (
    <div
      className="mt-2 flex h-1 overflow-hidden rounded-pill bg-line"
      aria-hidden="true"
    >
      <motion.div
        className="h-full bg-up-600/70"
        style={{ width: `${(call / total) * 100}%`, transformOrigin: 'left' }}
        initial={{ scaleX: 0 }}
        animate={{ scaleX: 1 }}
        transition={{ duration: 0.5, delay, ease: EASE }}
      />
      <motion.div
        className="h-full bg-down-600/70"
        style={{ width: `${(put / total) * 100}%`, transformOrigin: 'right' }}
        initial={{ scaleX: 0 }}
        animate={{ scaleX: 1 }}
        transition={{ duration: 0.5, delay, ease: EASE }}
      />
    </div>
  );
}

function Tile({
  label,
  value,
  caption,
  bar,
  icon,
}: {
  label: string;
  value: string;
  caption: string;
  bar?: ReactNode;
  icon?: ReactNode;
}) {
  return (
    <motion.div
      className="rounded-md bg-paper-2 px-3 py-2"
      variants={{
        hidden: { opacity: 0, y: 6 },
        show: { opacity: 1, y: 0, transition: { duration: 0.24, ease: EASE } },
      }}
    >
      <p className="flex items-center gap-1 font-sans text-micro text-ink-400">
        {icon}
        {label}
      </p>
      <p className="mt-0.5 font-mono text-data-m font-medium text-ink-800 tnum">
        {value}
      </p>
      <p className="mt-0.5 text-micro text-ink-400">{caption}</p>
      {bar}
    </motion.div>
  );
}

/** 单侧值文案：缺失显「—」，绝不折 0（与逐行同一纪律）。 */
const sideText = (value: number | null, prefix = ''): string =>
  value === null ? '—' : `${prefix}${fmtCompact(value)}`;

/** 两侧齐全才叫「总计」；单侧缺失时数字仍给已知侧合计，但 caption 用
 *  「C — · P …」明示缺口（GPT-5.6-Pro 审计：sumSides 把缺失当 0 的问题）。 */
function tileParts(
  call: number | null,
  put: number | null,
  prefix = '',
): { value: string; partial: boolean } {
  const total = sumSides(call, put);
  return {
    value: total === null ? '—' : `${prefix}${fmtCompact(total)}`,
    partial: total !== null && (call === null || put === null),
  };
}

export default function SummaryTiles({
  totals,
  alertCount,
}: {
  totals: ChainTotals;
  alertCount: number;
}) {
  const vol = tileParts(totals.callVol, totals.putVol);
  const oi = tileParts(totals.callOi, totals.putOi);
  const premium = tileParts(totals.callPremium, totals.putPremium, '$');
  const sidesCaption = (call: number | null, put: number | null, prefix = '') =>
    `C ${sideText(call, prefix)} · P ${sideText(put, prefix)}`;
  return (
    <motion.div
      className="mt-3 grid grid-cols-2 gap-2.5 md:grid-cols-4"
      initial="hidden"
      animate="show"
      variants={{ show: { transition: { staggerChildren: 0.05 } } }}
    >
      <Tile
        label={vol.partial ? t('已知成交量（数据不完整）') : t('总成交量')}
        value={vol.value}
        caption={
          vol.partial
            ? sidesCaption(totals.callVol, totals.putVol)
            : t('C/P 比 {ratio}', { ratio: fmtRatio(cpRatio(totals.callVol, totals.putVol)) })
        }
        bar={<SplitBar call={totals.callVol} put={totals.putVol} delay={0.15} />}
      />
      <Tile
        label={oi.partial ? t('已知持仓量（数据不完整）') : t('总持仓量')}
        value={oi.value}
        caption={
          oi.partial
            ? sidesCaption(totals.callOi, totals.putOi)
            : t('C/P 比 {ratio}', { ratio: fmtRatio(cpRatio(totals.callOi, totals.putOi)) })
        }
        bar={<SplitBar call={totals.callOi} put={totals.putOi} delay={0.2} />}
      />
      <Tile
        label={premium.partial ? t('已知权利金流（数据不完整）') : t('估算权利金流')}
        value={premium.value}
        caption={
          premium.value === '—'
            ? t('缺买卖价，不可估算')
            : sidesCaption(totals.callPremium, totals.putPremium, '$')
        }
        bar={<SplitBar call={totals.callPremium} put={totals.putPremium} delay={0.25} />}
      />
      <Tile
        label={t('异动合约')}
        value={String(alertCount)}
        caption={t('量持比 > 3 或全部新开仓')}
        icon={<Icon name="bolt" size={11} className="text-warn-600" />}
      />
    </motion.div>
  );
}
