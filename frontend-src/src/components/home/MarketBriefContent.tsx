/**
 * 「市场综合研判」正文：覆盖条 → 一句话结论 → 四段（xl 两列）→ 复盘与外部来源（可折叠）→ 口径说明。
 * 模型写的正文原样显示、不经 t()；缺字段显「—」。
 */
import { useId, useState, type ReactNode } from 'react';
import { Link } from 'react-router';
import type { BriefSource, MarketBrief } from '@/api/modules/marketBrief';
import Icon from '@/components/icons';
import CollapsePresence from '@/components/shared/CollapsePresence';
import InfoHint from '@/components/shared/InfoHint';
import SoftBadge from '@/components/shared/SoftBadge';
import SourceNote from '@/components/shared/SourceNote';
import { SCORE_HINTS } from '@/lib/scoreHints';
import { cn } from '@/lib/utils';
import {
  BREADTH_LABEL,
  BREADTH_TONE,
  CHANGE_LABEL,
  CHANGE_TONE,
  PRICED_IN_LABEL,
  PRICED_IN_TONE,
  REGIME_LABEL,
  REGIME_TONE,
  SUFFICIENCY_LABEL,
  SUFFICIENCY_TONE,
  VERDICT_LABEL,
  VERDICT_TONE,
  coverageItems,
} from './marketBriefText';
import { t } from '../../i18n/core.ts';

const MISSING = '—';

/* 代码小标签与新闻流 TickerChip 同一副样式；触屏下用透明伪元素把点按区补到 44px 高，外观不变。 */
const TICKER_LINK =
  'soft-badge relative inline-flex items-center bg-brand-50 px-1.5 py-0.5 tnum text-[12px] leading-[16px] font-medium text-brand-700 transition-colors duration-fast hover:bg-brand-100 [@media(pointer:coarse)]:after:absolute [@media(pointer:coarse)]:after:inset-x-0 [@media(pointer:coarse)]:after:-inset-y-3.5 [@media(pointer:coarse)]:after:content-[""]';

function Dot() {
  return <span className="mt-2 size-1.5 shrink-0 rounded-full bg-ink-300" aria-hidden="true" />;
}

function Bullets({ items, className }: { items: string[]; className?: string }) {
  if (items.length === 0) return null;
  return (
    <ul className={cn('space-y-1.5', className)}>
      {items.map((item, index) => (
        <li key={index} className="flex gap-2 text-body-s text-ink-600">
          <Dot />
          <span className="min-w-0 break-words">{item}</span>
        </li>
      ))}
    </ul>
  );
}

function Part({ title, badge, children }: { title: string; badge?: ReactNode; children: ReactNode }) {
  return (
    <div className="min-w-0">
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
        <h3 className="text-body-s font-medium text-ink-900">{title}</h3>
        {badge}
      </div>
      {children}
    </div>
  );
}

function Summary({ text }: { text: string | null }) {
  return (
    <p className={cn('mt-2 break-words text-body', text ? 'text-ink-800' : 'text-ink-400')}>{text ?? MISSING}</p>
  );
}

function SubLabel({ children, className }: { children: ReactNode; className?: string }) {
  return <p className={cn('text-micro text-ink-500', className)}>{children}</p>;
}

function hostOf(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, '');
  } catch {
    return url;
  }
}

function SourceList({ sources }: { sources: BriefSource[] }) {
  return (
    <ul className="space-y-0.5">
      {sources.map((source) => {
        const host = hostOf(source.url);
        return (
          <li key={source.url} className="flex min-w-0 flex-wrap items-center gap-x-2">
            <a
              href={source.url}
              target="_blank"
              rel="noreferrer"
              className="touch-target inline-flex min-h-9 min-w-0 items-center gap-1.5 text-body-s text-brand-700 transition-colors duration-fast hover:text-brand-600"
            >
              <span className="min-w-0 [overflow-wrap:anywhere]">{source.title ?? host}</span>
              <Icon name="external" size={12} className="shrink-0" />
            </a>
            {source.title && <span className="text-micro text-ink-400">{host}</span>}
          </li>
        );
      })}
    </ul>
  );
}

/** 折叠行：按钮报告展开状态，内容用 CollapsePresence 收放（收起播完再卸载）。 */
function Disclosure({ label, children }: { label: string; children: ReactNode }) {
  const [open, setOpen] = useState(false);
  const panelId = useId();
  return (
    <div className="border-t border-line">
      <button
        type="button"
        aria-expanded={open}
        aria-controls={open ? panelId : undefined}
        onClick={() => setOpen((value) => !value)}
        className="touch-target flex min-h-10 w-full items-center justify-between gap-3 py-2 text-left text-caption text-ink-500 transition-colors duration-fast hover:text-ink-800"
      >
        <span className="min-w-0">{label}</span>
        <Icon
          name="chevron-down"
          size={14}
          className={cn('shrink-0 transition-transform duration-ui', open && 'rotate-180')}
        />
      </button>
      <CollapsePresence open={open} id={panelId}>
        <div className="pb-4">{children}</div>
      </CollapsePresence>
    </div>
  );
}

export default function MarketBriefContent({ brief, year }: { brief: MarketBrief; year?: string }) {
  const { result } = brief;
  const coverage = coverageItems(brief.coverage, year);
  const sufficiency = result.evidence_sufficiency
    ? SUFFICIENCY_LABEL[result.evidence_sufficiency]
    : t('证据充分度：—');
  const hasSectorNews = result.sectors.length > 0 || result.key_news.length > 0;
  const hasWatch = result.watch_items.length > 0 || result.invalidators.length > 0;

  return (
    <div>
      {coverage.length > 0 && (
        <div className="data-coverage-strip flex flex-wrap items-center gap-x-4 gap-y-1 text-micro text-ink-500">
          {coverage.map((item) => (
            <span key={item.key} className={cn('inline-flex items-center gap-1.5', item.warn && 'text-warn-700')}>
              {item.label}
              <span className={cn('font-medium tnum', item.warn ? 'text-warn-700' : 'text-ink-700')}>{item.value}</span>
            </span>
          ))}
        </div>
      )}

      <div className="mt-3 flex flex-wrap items-start gap-x-4 gap-y-2">
        <p className={cn('min-w-0 flex-1 basis-[22rem] break-words text-h2', result.headline ? 'text-ink-900' : 'text-ink-400')}>
          {result.headline ?? MISSING}
        </p>
        <div className="flex flex-wrap items-center gap-2 pt-1">
          <SoftBadge tone={REGIME_TONE[result.regime]}>{REGIME_LABEL[result.regime]}</SoftBadge>
          <SoftBadge tone={result.evidence_sufficiency ? SUFFICIENCY_TONE[result.evidence_sufficiency] : 'neutral'}>
            {sufficiency}
            <InfoHint hint={SCORE_HINTS.marketBriefSufficiency} size={11} className="ml-1" />
          </SoftBadge>
        </div>
      </div>

      <div className="mt-6 grid gap-6 xl:grid-cols-2">
        <Part
          title={t('大盘与内部结构')}
          badge={
            <SoftBadge tone={BREADTH_TONE[result.internals.breadth_vs_index]} title={t('广度与指数是否一致')}>
              {BREADTH_LABEL[result.internals.breadth_vs_index]}
            </SoftBadge>
          }
        >
          <Summary text={result.internals.summary} />
          <Bullets items={result.internals.points} className="mt-3" />
        </Part>

        <Part
          title={t('宏观与跨资产验证')}
          badge={
            <SoftBadge tone={VERDICT_TONE[result.macro_check.verdict]} title={t('宏观与跨资产是否支持当前的股票叙事')}>
              {VERDICT_LABEL[result.macro_check.verdict]}
            </SoftBadge>
          }
        >
          <Summary text={result.macro_check.summary} />
          <Bullets items={result.macro_check.points} className="mt-3" />
        </Part>

        <Part title={t('行业与关键新闻')}>
          {!hasSectorNews && <Summary text={null} />}
          {result.sectors.length > 0 && (
            <>
              <SubLabel className="mt-2">{t('行业')}</SubLabel>
              <ul className="mt-1.5 space-y-2.5">
                {result.sectors.map((sector, index) => (
                  <li key={`${sector.name}-${index}`}>
                    <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                      <span className="text-body-s font-medium text-ink-800">{sector.name}</span>
                      <SoftBadge tone={CHANGE_TONE[sector.change]}>{CHANGE_LABEL[sector.change]}</SoftBadge>
                    </div>
                    {sector.note && <p className="mt-0.5 break-words text-body-s text-ink-600">{sector.note}</p>}
                  </li>
                ))}
              </ul>
            </>
          )}
          {result.key_news.length > 0 && (
            <>
              <SubLabel className={result.sectors.length > 0 ? 'mt-4' : 'mt-2'}>{t('关键新闻')}</SubLabel>
              <ul className="mt-1.5 space-y-3">
                {result.key_news.map((news, index) => (
                  <li key={`${news.evidence_id ?? ''}-${index}`}>
                    <div className="flex items-start justify-between gap-3">
                      <p className="min-w-0 break-words text-body-s font-medium text-ink-800">{news.title_zh}</p>
                      <SoftBadge tone={PRICED_IN_TONE[news.priced_in]} className="mt-0.5 shrink-0" title={t('是否已反映在价格中')}>
                        {PRICED_IN_LABEL[news.priced_in]}
                      </SoftBadge>
                    </div>
                    {news.what_is_new && (
                      <p className="mt-0.5 break-words text-body-s text-ink-600">{news.what_is_new}</p>
                    )}
                    {news.tickers.length > 0 && (
                      <div className="mt-1.5 flex flex-wrap gap-1.5">
                        {news.tickers.map((ticker) => (
                          <Link key={ticker} to={`/stock/${encodeURIComponent(ticker)}`} className={TICKER_LINK}>
                            {ticker}
                          </Link>
                        ))}
                      </div>
                    )}
                  </li>
                ))}
              </ul>
            </>
          )}
        </Part>

        <Part title={t('后续观察与反证')}>
          {!hasWatch && <Summary text={null} />}
          {result.watch_items.length > 0 && (
            <ul className="mt-2 space-y-3">
              {result.watch_items.map((item, index) => (
                <li key={index} className="flex gap-2">
                  <Dot />
                  <div className="min-w-0 text-body-s">
                    <p className="break-words font-medium text-ink-800">{item.what}</p>
                    {item.why && <p className="mt-0.5 break-words text-ink-600">{item.why}</p>}
                    {item.revise_if && (
                      <p className="mt-0.5 break-words text-ink-600">
                        <span className="text-ink-500">{t('改判条件')}</span> {item.revise_if}
                      </p>
                    )}
                  </div>
                </li>
              ))}
            </ul>
          )}
          {result.invalidators.length > 0 && (
            <div className="mt-4 rounded-lg bg-paper-2 px-3 py-2.5">
              <p className="text-caption text-ink-700">{t('出现以下情况应撤回当前判断')}</p>
              <Bullets items={result.invalidators} className="mt-1.5" />
            </div>
          )}
        </Part>
      </div>

      {(result.prior_review || brief.externalSources.length > 0) && (
        <div className="mt-6">
          {result.prior_review && (
            <Disclosure label={t('上一份研判的复盘')}>
              <p className="break-words text-body-s text-ink-600">{result.prior_review}</p>
            </Disclosure>
          )}
          {brief.externalSources.length > 0 && (
            <Disclosure label={t('外部来源 {n}', { n: brief.externalSources.length })}>
              <SourceList sources={brief.externalSources} />
            </Disclosure>
          )}
        </div>
      )}

      <SourceNote
        mark={false}
        className={result.prior_review || brief.externalSources.length > 0 ? undefined : 'mt-6'}
        text={t('程序汇总行情、广度、宏观、行业、新闻与日历证据，模型负责解释与找矛盾')}
      />
    </div>
  );
}
