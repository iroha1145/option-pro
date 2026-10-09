/**
 * 过滤器条：ticker / window_hours / classification / analysis_status / min_confidence / min_abs_impact / multi_source_only
 * 常显：股票代码、时间范围、清空条件、条数。
 * 更多筛选：影响方向、分析状态、置信度门槛、影响分门槛、多处报道；折叠时已选条件仍以徽标列出。
 */
import { useId, useState, type CSSProperties } from 'react';
import { motion } from 'framer-motion';
import Segmented from '@/components/shared/Segmented';
import MenuSelect from '@/components/shared/MenuSelect';
import SoftBadge from '@/components/shared/SoftBadge';
import Icon from '@/components/icons';
import { cn } from '@/lib/utils';
import Switch from '@/components/shared/Switch';
import { SPRING_POP } from '@/lib/motion';
import type { NewsAnalysisStatus, NewsClassification } from './api';
import { catalystsContract } from './api';
import { prefetchDefaultFeed } from './feedPrefetch';
import { DEFAULT_FILTERS, type CatalystFilters } from './filters';
import { t } from '../../i18n/core.ts';

const STATUS_OPTIONS: { value: '' | NewsAnalysisStatus; label: string }[] = [
  { value: '', label: t('全部状态') },
  { value: 'pending', label: t('未分析') },
  { value: 'queued', label: t('排队中') },
  { value: 'in_progress', label: t('分析中') },
  { value: 'completed', label: t('已分析') },
  { value: 'insufficient_context', label: t('信息不足') },
  { value: 'failed', label: t('分析失败') },
];

const CLASSIFICATION_LABEL: Record<NewsClassification, string> = {
  bullish: t('利多'),
  bearish: t('利空'),
  neutral: t('中性'),
};

function StatusDropdown({ value, onChange }: { value: '' | NewsAnalysisStatus; onChange: (v: '' | NewsAnalysisStatus) => void }) {
  return (
    <MenuSelect
      value={value}
      onChange={onChange}
      options={STATUS_OPTIONS}
      ariaLabel={t('分析状态')}
      triggerClassName={
        value ? 'border-brand-400 bg-brand-50 px-2.5 text-brand-700' : 'px-2.5 text-ink-500 hover:text-ink-800'
      }
    />
  );
}

function LabeledSlider({
  label,
  value,
  min,
  max,
  step,
  format,
  onChange,
}: {
  label: string;
  value: number;
  min: number;
  max: number;
  step: number;
  format: (v: number) => string;
  onChange: (v: number) => void;
}) {
  /* 外观与「算法与图层」的滑杆同一套 .ft-range（原生 range 只换皮，保住 role=slider）。 */
  const fill = max > min ? ((value - min) / (max - min)) * 100 : 0;
  return (
    <div className="flex items-center gap-2">
      <span className="whitespace-nowrap text-micro text-ink-400">{label}</span>
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        className="ft-range w-24 shrink-0 cursor-pointer"
        style={{ '--fill': `${fill}%` } as CSSProperties}
        aria-label={label}
      />
      <span className={cn('w-14 whitespace-nowrap text-micro tnum', value > 0 ? 'text-brand-600' : 'text-ink-400')}>
        {format(value)}
      </span>
    </div>
  );
}

interface FilterBarProps {
  filters: CatalystFilters;
  onChange: (f: CatalystFilters) => void;
  total: number | null;
  filtered: boolean;
}

export default function FilterBar({ filters, onChange, total, filtered }: FilterBarProps) {
  /* 展开区的控件只在打开时挂载：分段控件的滑块在隐藏容器里量不到尺寸。 */
  const [moreOpen, setMoreOpen] = useState(false);
  const morePanelId = useId();
  const set = (patch: Partial<CatalystFilters>) => onChange({ ...filters, ...patch });

  /* 收进「更多筛选」的条件：折叠后仍列出来，读者不会忘了还有条件在生效。 */
  const moreSummary = [
    filters.classification ? CLASSIFICATION_LABEL[filters.classification] : null,
    filters.analysisStatus ? STATUS_OPTIONS.find((o) => o.value === filters.analysisStatus)?.label ?? filters.analysisStatus : null,
    filters.minConfidence > 0 ? `${t('置信度 ≥')} ${Math.round(filters.minConfidence * 100)}%` : null,
    filters.minAbsImpact > 0 ? `${t('影响分 ≥')} ${filters.minAbsImpact.toFixed(1)}` : null,
    filters.multiSourceOnly ? t('多处报道') : null,
  ].filter((label): label is string => label !== null);

  const activeCount =
    (filters.ticker ? 1 : 0) +
    moreSummary.length +
    (filters.themeId ? 1 : 0) +
    (filters.windowHours !== DEFAULT_FILTERS.windowHours ? 1 : 0);

  return (
    <div className="mt-5">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2.5" data-testid="catalyst-filter-row">
        {/* ticker 过滤 */}
        <div className="flex items-center gap-1.5">
          <div className="relative">
            <Icon name="search" size={13} className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-ink-400" />
            <input
              value={filters.ticker}
              onChange={(e) => set({ ticker: e.target.value.toUpperCase().replace(/[^A-Z0-9.^-]/g, '').slice(0, 12) })}
              placeholder={t("股票代码")}
              className="h-8 w-28 rounded-md border border-line bg-card pl-7 pr-2 tnum text-caption text-ink-800 placeholder:text-ink-400 focus:border-brand-400 focus:outline-none"
              aria-label={t("按股票代码筛选")}
            />
          </div>
          {filters.ticker && (
            <button onClick={() => set({ ticker: '' })} className="rounded-sm p-1 text-ink-400 hover:text-ink-600" aria-label={t("取消股票代码筛选")}>
              <Icon name="x" size={12} />
            </button>
          )}
        </div>

        <Segmented
          options={[
            { value: '6', label: t('6 小时') },
            { value: '24', label: t('24 小时') },
            { value: '72', label: t('3 天') },
            { value: '168', label: t('7 天') },
          ]}
          value={String(filters.windowHours)}
          onChange={(v) => set({ windowHours: Number(v) })}
          onOptionIntent={(v) => {
            if (v === '24' && filters.windowHours !== 24) prefetchDefaultFeed(24, filters);
          }}
        />

        {/* 清空条件与条数常显，不随展开区收起 */}
        <div className="ml-auto flex items-center gap-3">
          {activeCount > 0 && (
            <button
              onClick={() => onChange({ ...DEFAULT_FILTERS })}
              className="touch-target flex items-center gap-1 rounded-pill border border-line bg-card px-2 py-1.5 text-micro text-ink-500 shadow-btn transition-colors duration-fast hover:border-down-600/40 hover:text-down-700"
            >
              <Icon name="x" size={11} />
              {t('清空条件')}
            </button>
          )}
          <CountNote total={total} filtered={filtered} />
        </div>
      </div>

      {/* 更多筛选：收起时已选条件以徽标列出 */}
      <div className="mt-3 flex flex-wrap items-center gap-x-3 gap-y-2">
        <button
          type="button"
          onClick={() => setMoreOpen((open) => !open)}
          aria-expanded={moreOpen}
          aria-controls={moreOpen ? morePanelId : undefined}
          className={cn(
            'disclosure-trigger flex items-center gap-2 rounded-pill border px-3 py-1.5 text-caption font-medium shadow-btn transition-colors duration-fast',
            moreSummary.length > 0 ? 'border-brand-400 bg-brand-50 text-brand-700' : 'border-line bg-card text-ink-600',
          )}
        >
          <Icon name="filter-funnel" size={14} />
          {t('更多筛选')}
          <Icon name="chevron-down" size={12} className={cn('transition-transform duration-fast', moreOpen && 'rotate-180')} />
        </button>
        {moreSummary.length > 0 && (
          <span className="flex min-w-0 flex-wrap items-center gap-1.5" data-testid="catalyst-more-filters-summary">
            {moreSummary.map((label) => (
              <SoftBadge key={label}>{label}</SoftBadge>
            ))}
          </span>
        )}
      </div>

      {moreOpen && (
        <div
          id={morePanelId}
          className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-3 rounded-lg border border-line bg-card-warm/70 px-3.5 py-3"
        >
          <Segmented
            ariaLabel={t('影响方向')}
            options={[
              { value: '', label: t('全部') },
              { value: 'bullish', label: t('利多') },
              { value: 'bearish', label: t('利空') },
              { value: 'neutral', label: t('中性') },
            ]}
            value={filters.classification}
            onChange={(v) => set({ classification: v as '' | NewsClassification })}
          />

          <StatusDropdown value={filters.analysisStatus} onChange={(v) => set({ analysisStatus: v })} />

          <LabeledSlider
            label={t("置信度 ≥")}
            value={Math.round(filters.minConfidence * 100)}
            min={0}
            max={90}
            step={5}
            format={(v) => (v > 0 ? `${v}%` : t('不限'))}
            onChange={(v) => set({ minConfidence: v / 100 })}
          />

          <LabeledSlider
            label={t("影响分 ≥")}
            value={filters.minAbsImpact}
            min={0}
            max={5}
            step={0.5}
            format={(v) => (v > 0 ? v.toFixed(1) : t('不限'))}
            onChange={(v) => set({ minAbsImpact: v })}
          />

          {/* 多处报道开关 */}
          <label className="flex cursor-pointer items-center gap-2">
            <Switch
              checked={filters.multiSourceOnly}
              onToggle={() => set({ multiSourceOnly: !filters.multiSourceOnly })}
            />
            <span className={cn('whitespace-nowrap text-micro', filters.multiSourceOnly ? 'text-ink-800' : 'text-ink-400')}>{t('多处报道')}</span>
          </label>
        </div>
      )}

      {/* 激活的主题过滤 chip（热点带带入） */}
      {filters.themeId && (
        <motion.div
          initial={{ scale: 0.9, opacity: 0 }}
          animate={{ scale: 1, opacity: 1 }}
          transition={SPRING_POP}
          className="mt-2.5 flex items-center gap-2"
        >
          <button
            type="button"
            onClick={() => set({ themeId: null })}
            aria-label={t('取消主题筛选')}
            className="control-button"
          >
            <Icon name="flame-line" size={12} />
            {t('主题：')}{catalystsContract.themeName(filters.themeId)}
            <Icon name="x" size={11} />
          </button>
        </motion.div>
      )}
    </div>
  );
}

function CountNote({ total, filtered }: { total: number | null; filtered: boolean }) {
  return (
    <p className="text-micro text-ink-400 tnum">
      {total === null ? '—' : t('{n} 条', { n: total })}
      {filtered && total !== null && <span className="text-ink-400"> {t('· 已筛选')}</span>}
    </p>
  );
}
