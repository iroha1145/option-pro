import { Link } from 'react-router';
import { motion } from 'framer-motion';
import { DUR_UI, EASE_PAPER } from '@/lib/motion';
import { cn } from '@/lib/utils';
import { fmtPct } from '@/lib/format';
import TickerLogo from '@/components/shared/TickerLogo';
import InfoHint from '@/components/shared/InfoHint';
import MacroFitBadge from '@/components/shared/MacroFitBadge';
import { SCORE_HINTS, macroShadowHint } from '@/lib/scoreHints';
import { MACRO_QUADRANT_LABEL, macroQuadrant } from '@/lib/macroFit';
import { diagnosticCoverageStatus } from '@/lib/eodDiagnostics';
import Icon from '@/components/icons';
import type { SectorVm } from './model';
import { periodLabel, scoreSourceLabel } from './model';
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
          'mt-1 truncate metric-value text-data-l text-ink-800',
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
  const quadrant = macroQuadrant(sector.avgStrength, sector.macroFit);
  return (
    <motion.section
      key={sector.id}
      initial={{ height: 0, opacity: 0 }}
      animate={{ height: 'auto', opacity: 1 }}
      exit={{ height: 0, opacity: 0 }}
      transition={{ duration: DUR_UI, ease: EASE_PAPER }}
      className="overflow-hidden"
      aria-label={t('{name} 行业详情', { name: sector.name })}
    >
      <div className="card-surface mt-6 p-4 md:p-6">
        <div className="flex flex-wrap items-baseline justify-between gap-2 border-b border-line pb-3">
          <div>
            <p className="eyebrow">{t('行业详情')}</p>
            <h2 className="mt-1 font-display text-[18px] font-medium leading-[24px] text-ink-900">
              {sector.name}
            </h2>
          </div>
          <Link
            to={`/screener?sector=${encodeURIComponent(sector.id)}`}
            className="flex items-center gap-1 text-caption text-brand-600 transition-colors duration-fast hover:text-brand-500"
          >
            {t('查看条件选股')}
            <Icon name="arrow-up-right" size={12} />
          </Link>
        </div>

        <dl className="mt-4 grid grid-cols-3 gap-2 sm:grid-cols-4 sm:gap-4">
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
                {t('平均评分')}
                <InfoHint hint={SCORE_HINTS.sectorFullStrength} side="bottom" size={11} className="ml-1" />
              </>
            }
            value={sector.avgStrength?.toFixed(1) ?? '—'}
          />
          <Metric
            label={t("统计覆盖")}
            value={`${sector.coveredCount ?? '—'} / ${sector.memberCount}`}
          />
          {/* 与平均强度并列、不合成一个数：技术强但宏观逆风、宏观先改善而价格没跟上，
              正是两者分开看才看得出来。不传 status：没有强度聚合不等于宏观快照缺失。 */}
          <div className="col-span-3 min-w-0 border-t border-line pt-3 sm:col-span-1 sm:border-l sm:border-t-0 sm:pl-4 sm:pt-0">
            <dt className="flex items-center text-micro text-ink-400">
              {t('宏观适配')}
              <InfoHint hint={macroShadowHint()} side="bottom" size={11} className="ml-1" />
            </dt>
            <dd className="mt-1.5 flex flex-col items-start gap-1">
              <MacroFitBadge score={sector.macroFit} tailwind={sector.macroTailwind} />
              {quadrant && <span className="text-micro text-ink-400">{t(MACRO_QUADRANT_LABEL[quadrant])}</span>}
            </dd>
          </div>
        </dl>
        <p className="mt-2 text-micro text-ink-400">
          {t('有评分 {scored} / {total}', { scored: sector.scoredCount ?? '—', total: sector.memberCount })}
          {sector.scoreDataThrough ? ` · ${t('评分截至 {date}', { date: sector.scoreDataThrough })}` : ''}
          {scoreSourceLabel(sector.scoreSourceStatus) ? ` · ${scoreSourceLabel(sector.scoreSourceStatus)}` : ''}
        </p>

        {sector.scoreBasis === 'full_theme_balanced_mid_A' && (
          <div className="mt-2 space-y-1 text-micro text-ink-500" data-testid="sector-full-score-basis">
            <p>{t('均分采用全体有有效评分的成员，固定为均衡、中期、趋势质量。')}</p>
            <p>{t('基准收益')} {sector.benchmarkTicker ?? '—'} {sector.benchmarkReturn != null ? fmtPct(sector.benchmarkReturn) : '—'}
              {' · '}{t('较基准')} {sector.excessReturn != null ? `${sector.excessReturn >= 0 ? '+' : ''}${sector.excessReturn.toFixed(2)}` : '—'} {t('个百分点')}</p>
            {Object.keys(sector.scoreMissingReasons ?? {}).length > 0 && (
              <p>{t('缺失原因')} · {Object.entries(sector.scoreMissingReasons ?? {}).map(([reason, count]) => `${diagnosticCoverageStatus(reason)} ${count}`).join(' · ')}</p>
            )}
          </div>
        )}

        <div className="mt-5 grid grid-cols-1 gap-6 lg:grid-cols-2">
          <div>
            <p className="eyebrow">{t('高分股票')}</p>
            {sector.leaders.length === 0 ? (
              <p className="mt-3 text-body-s text-ink-400">
                {t('暂无行业高分股票数据。')}
              </p>
            ) : (
              <ul className="mt-2 divide-y divide-line">
                {sector.leaders.map((leader, index) => (
                  <li key={leader.ticker}>
                    <button
                      type="button"
                      onClick={() => onOpenTicker(leader.ticker)}
                      className="group flex min-h-11 w-full items-center gap-3 py-2 text-left transition-colors duration-fast hover:bg-paper-2"
                    >
                      <span className="w-5 shrink-0 text-micro text-ink-400 tnum">
                        {String(index + 1).padStart(2, '0')}
                      </span>
                      <TickerLogo ticker={leader.ticker} size={26} />
                      <span className="tnum text-body-s font-medium text-ink-800">
                        {leader.ticker}
                      </span>
                      <span className="ml-auto text-micro text-ink-400">
                        {t('评分')}
                      </span>
                      <span className="w-12 text-right text-data-m text-ink-900 tnum">
                        {leader.score?.toFixed(1) ?? '—'}
                      </span>
                      <Icon
                        name="arrow-up-right"
                        size={12}
                        className="text-ink-400 transition-colors duration-fast group-hover:text-brand-600"
                      />
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>

          <div>
            <div className="flex items-center justify-between gap-3">
              <p className="eyebrow">{t('行业成分股')}</p>
              <span className="text-micro text-ink-400 tnum">
                {sector.memberCount} {t('只')}
              </span>
            </div>
            {sector.tickers.length === 0 ? (
              <p className="mt-3 text-body-s text-ink-400">
                {t('暂未获取到该行业的成分股。')}
              </p>
            ) : (
              <>
              <div className="mt-3 grid grid-cols-3 gap-2 sm:grid-cols-4">
                {/* 只展示前 12 个：截断要标出来（下方注脚），旁边的总数才不骗人 */}
                {sector.tickers.slice(0, 12).map((ticker) => (
                  <button
                    key={ticker}
                    type="button"
                    onClick={() => onOpenTicker(ticker)}
                    className="min-w-0 rounded-md border border-line bg-card-warm px-2 py-2 text-center tnum text-caption text-ink-700 transition-colors duration-fast hover:border-brand-400 hover:text-brand-700"
                  >
                    <span className="block truncate">{ticker}</span>
                  </button>
                ))}
              </div>
              {sector.tickers.length > 12 && (
                <p className="mt-2 text-micro text-ink-400">
                  {t('仅显示前 12 只 · 共 {n} 只股票', { n: sector.tickers.length })}
                </p>
              )}
              </>
            )}
          </div>
        </div>


      </div>
    </motion.section>
  );
}
