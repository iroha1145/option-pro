import { Link } from 'react-router';
import { motion } from 'framer-motion';
import { cn } from '@/lib/utils';
import { fmtPct } from '@/lib/format';
import TickerLogo from '@/components/shared/TickerLogo';
import InfoHint from '@/components/shared/InfoHint';
import { SCORE_HINTS } from '@/lib/scoreHints';
import Icon from '@/components/icons';
import type { SectorVm } from './model';
import { periodLabel } from './model';
import { t } from '../../i18n/core.ts';

interface DetailBandProps {
  sector: SectorVm;
  onOpenTicker: (ticker: string) => void;
}

function Metric({
  label,
  value,
  tone,
}: {
  label: React.ReactNode;
  value: React.ReactNode;
  tone?: 'up' | 'down';
}) {
  return (
    <div className="min-w-0 border-l border-line pl-3 first:border-0 first:pl-0 sm:pl-4">
      <dt className="text-micro text-ink-400">{label}</dt>
      <dd
        className={cn(
          'mt-1 truncate font-mono text-data-l font-semibold text-ink-800 tnum',
          tone === 'up' && 'text-up-700',
          tone === 'down' && 'text-down-700',
        )}
      >
        {value}
      </dd>
    </div>
  );
}

export default function DetailBand({
  sector,
  onOpenTicker,
}: DetailBandProps) {
  return (
    <motion.section
      key={sector.id}
      initial={{ height: 0, opacity: 0 }}
      animate={{ height: 'auto', opacity: 1 }}
      exit={{ height: 0, opacity: 0 }}
      transition={{ duration: 0.32, ease: [0.16, 1, 0.3, 1] }}
      className="overflow-hidden"
      aria-label={t('{name} 板块详情', { name: sector.name })}
    >
      <div className="card-surface mt-6 p-4 md:p-6">
        <div className="flex flex-wrap items-baseline justify-between gap-2 border-b border-line pb-3">
          <div>
            <p className="eyebrow">{t('板块详情 · 成分股汇总')}</p>
            <h2 className="mt-1 font-display text-[18px] font-semibold leading-[24px] text-ink-900">
              {sector.name}
            </h2>
          </div>
          <Link
            to={`/screener?sector=${encodeURIComponent(sector.id)}`}
            className="flex items-center gap-1 text-caption text-brand-600 transition-colors hover:text-brand-500"
          >
            {t('查看该板块扫描结果')}
            <Icon name="arrow-up-right" size={12} />
          </Link>
        </div>

        <dl className="mt-4 grid grid-cols-3 gap-2 sm:gap-4">
          <Metric
            label={t('{period}平均收益', { period: periodLabel(sector.period) })}
            value={sector.avgReturn !== null ? fmtPct(sector.avgReturn) : '—'}
            tone={
              sector.avgReturn === null
                ? undefined
                : sector.avgReturn >= 0
                  ? 'up'
                  : 'down'
            }
          />
          <Metric
            label={
              <>
                {t('平均强度')}
                <InfoHint hint={SCORE_HINTS.avgStrength} side="bottom" size={11} className="ml-1" />
              </>
            }
            value={sector.avgStrength?.toFixed(1) ?? '—'}
          />
          <Metric
            label={t("统计覆盖")}
            value={`${sector.coveredCount ?? '—'} / ${sector.memberCount}`}
          />
        </dl>

        <div className="mt-5 grid grid-cols-1 gap-6 lg:grid-cols-2">
          <div>
            <p className="eyebrow">{t('强度领先标的')}</p>
            {sector.leaders.length === 0 ? (
              <p className="mt-3 text-body-s text-ink-400">
                {t('暂无领先标的数据。')}
              </p>
            ) : (
              <ul className="mt-2 divide-y divide-line">
                {sector.leaders.map((leader, index) => (
                  <li key={leader.ticker}>
                    <button
                      type="button"
                      onClick={() => onOpenTicker(leader.ticker)}
                      className="group flex min-h-11 w-full items-center gap-3 py-2 text-left transition-colors hover:bg-paper-2"
                    >
                      <span className="w-5 shrink-0 font-mono text-micro text-ink-300 tnum">
                        {String(index + 1).padStart(2, '0')}
                      </span>
                      <TickerLogo ticker={leader.ticker} size={26} />
                      <span className="font-mono text-body-s font-semibold text-ink-800">
                        {leader.ticker}
                      </span>
                      <span className="ml-auto text-micro text-ink-400">
                        {t('强度')}
                      </span>
                      <span className="w-12 text-right font-mono text-data-m font-semibold text-ink-800 tnum">
                        {leader.score?.toFixed(1) ?? '—'}
                      </span>
                      <Icon
                        name="arrow-up-right"
                        size={12}
                        className="text-ink-300 transition-colors group-hover:text-brand-600"
                      />
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>

          <div>
            <div className="flex items-center justify-between gap-3">
              <p className="eyebrow">{t('板块目录成分')}</p>
              <span className="font-mono text-micro text-ink-400 tnum">
                {sector.memberCount} {t('只')}
              </span>
            </div>
            {sector.tickers.length === 0 ? (
              <p className="mt-3 text-body-s text-ink-400">
                {t('板块目录尚未返回成分代码。')}
              </p>
            ) : (
              <div className="mt-3 grid grid-cols-3 gap-2 sm:grid-cols-4">
                {/* 只展示前 12 个：截断要标出来（下方注脚），旁边的总数才不骗人 */}
                {sector.tickers.slice(0, 12).map((ticker) => (
                  <button
                    key={ticker}
                    type="button"
                    onClick={() => onOpenTicker(ticker)}
                    className="min-w-0 rounded-md border border-line bg-card-warm px-2 py-2 text-center font-mono text-caption font-semibold text-ink-700 transition-colors hover:border-brand-400 hover:text-brand-700"
                  >
                    <span className="block truncate">{ticker}</span>
                  </button>
                ))}
              </div>
              {sector.tickers.length > 12 && (
                <p className="mt-2 text-micro text-ink-400">
                  {t('仅展示前 12 个 · 共 {n} 只成分', { n: sector.tickers.length })}
                </p>
              )}
            )}
          </div>
        </div>

        <div
          className="mt-5 flex items-start gap-2 border-t border-line pt-3 text-micro leading-5 text-ink-400"
          role="note"
        >
          <Icon name="flag" size={13} className="mt-0.5 shrink-0" />
          <span>
            {t('本页暂不提供板块资金流、相关性与历史趋势。')}
          </span>
        </div>
      </div>
    </motion.section>
  );
}
