/** 过滤器条：ticker / window_hours / classification / analysis_status / min_confidence / min_abs_impact / multi_source_only */
import { useEffect, useRef, useState } from 'react';
import { motion } from 'framer-motion';
import Segmented from '@/components/shared/Segmented';
import Icon from '@/components/icons';
import { cn } from '@/lib/utils';
import type { NewsAnalysisStatus, NewsClassification } from './api';
import { catalystsContract } from './api';
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

function StatusDropdown({ value, onChange }: { value: '' | NewsAnalysisStatus; onChange: (v: '' | NewsAnalysisStatus) => void }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (!ref.current?.contains(e.target as Node)) setOpen(false);
    };
    /* Escape 也能关（审计 2.5.8）：与 ScanHistoryPopover 同口径 */
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false);
    };
    document.addEventListener('mousedown', onDoc);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDoc);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);
  const current = STATUS_OPTIONS.find((o) => o.value === value) ?? STATUS_OPTIONS[0];
  return (
    <div ref={ref} className="relative">
      <button
        onClick={() => setOpen((v) => !v)}
        aria-haspopup="listbox"
        aria-expanded={open}
        className={cn(
          'flex h-8 items-center gap-1.5 rounded-md border px-2.5 text-caption shadow-btn transition-colors duration-fast',
          value ? 'border-brand-400 bg-brand-50 text-brand-700' : 'border-line bg-card text-ink-500 hover:text-ink-800',
        )}
      >
        <span className="font-mono">{current.label}</span>
        <Icon name="chevron-down" size={12} className={cn('transition-transform duration-200', open && 'rotate-180')} />
      </button>
      {open && (
        <div role="listbox" className="absolute left-0 top-9 z-30 w-36 overflow-hidden rounded-md border border-line bg-card shadow-sh-2">
          {STATUS_OPTIONS.map((o) => (
            <button
              key={o.value || 'all'}
              role="option"
              aria-selected={o.value === value}
              onClick={() => {
                onChange(o.value);
                setOpen(false);
              }}
              className={cn(
                'flex w-full items-center justify-between px-3 py-2 text-left text-caption transition-colors',
                o.value === value ? 'bg-brand-50 text-brand-600' : 'text-ink-500 hover:bg-paper-2',
              )}
            >
              {o.label}
              {o.value === value && <Icon name="check" size={12} />}
            </button>
          ))}
        </div>
      )}
    </div>
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
        className="h-1 w-24 cursor-pointer accent-brand-600"
        aria-label={label}
      />
      <span className={cn('w-14 whitespace-nowrap font-mono text-micro tnum', value > 0 ? 'text-brand-600' : 'text-ink-400')}>
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
  const [mobileOpen, setMobileOpen] = useState(false);
  const set = (patch: Partial<CatalystFilters>) => onChange({ ...filters, ...patch });

  const activeCount =
    (filters.ticker ? 1 : 0) +
    (filters.classification ? 1 : 0) +
    (filters.analysisStatus ? 1 : 0) +
    (filters.minConfidence > 0 ? 1 : 0) +
    (filters.minAbsImpact > 0 ? 1 : 0) +
    (filters.multiSourceOnly ? 1 : 0) +
    (filters.themeId ? 1 : 0) +
    (filters.windowHours !== DEFAULT_FILTERS.windowHours ? 1 : 0);

  const controls = (
    <>
      {/* ticker 过滤 */}
      <div className="flex items-center gap-1.5">
        <div className="relative">
          <Icon name="search" size={13} className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-ink-300" />
          <input
            value={filters.ticker}
            onChange={(e) => set({ ticker: e.target.value.toUpperCase().replace(/[^A-Z0-9.^-]/g, '').slice(0, 12) })}
            placeholder={t("代码过滤")}
            className="h-8 w-28 rounded-md border border-line bg-card pl-7 pr-2 font-mono text-caption text-ink-800 placeholder:text-ink-300 focus:border-brand-400 focus:outline-none"
            aria-label={t("按代码过滤")}
          />
        </div>
        {filters.ticker && (
          <button onClick={() => set({ ticker: '' })} className="rounded-sm p-1 text-ink-400 hover:text-ink-600" aria-label={t("清除代码过滤")}>
            <Icon name="x" size={12} />
          </button>
        )}
      </div>

      <Segmented
        options={[
          { value: '6', label: t('6 时') },
          { value: '24', label: t('24 时') },
          { value: '72', label: t('3 天') },
          { value: '168', label: t('7 天') },
        ]}
        value={String(filters.windowHours)}
        onChange={(v) => set({ windowHours: Number(v) })}
      />

      <Segmented
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

      {/* 多源确认开关 */}
      <button
        role="switch"
        aria-checked={filters.multiSourceOnly}
        onClick={() => set({ multiSourceOnly: !filters.multiSourceOnly })}
        className="flex items-center gap-2"
      >
        <span
          className={cn(
            'relative h-[18px] w-8 shrink-0 rounded-pill shadow-track transition-colors duration-ui',
            filters.multiSourceOnly ? 'bg-brand-600' : 'bg-ink-300',
          )}
        >
          <span
            className={cn(
              'absolute top-[2px] size-[14px] rounded-full bg-card shadow-knob transition-[left] duration-ui ease-paper',
              filters.multiSourceOnly ? 'left-[16px]' : 'left-[2px]',
            )}
          />
        </span>
        <span className={cn('whitespace-nowrap text-micro', filters.multiSourceOnly ? 'text-ink-800' : 'text-ink-400')}>{t('多源确认')}</span>
      </button>

      {activeCount > 0 && (
        <button
          onClick={() => onChange({ ...DEFAULT_FILTERS })}
          className="flex items-center gap-1 rounded-md border border-line bg-card px-2 py-1.5 text-micro text-ink-500 shadow-btn transition-colors duration-fast hover:border-down-600/40 hover:text-down-700"
        >
          <Icon name="x" size={11} />
          {t('清除过滤')}
        </button>
      )}
    </>
  );

  return (
    <div className="mt-5">
      {/* 移动：筛选折叠钮 */}
      <div className="flex items-center justify-between md:hidden">
        <button
          onClick={() => setMobileOpen((v) => !v)}
          aria-expanded={mobileOpen}
          className={cn(
            'flex items-center gap-2 rounded-md border px-3 py-2 text-caption font-medium shadow-btn transition-colors',
            activeCount > 0 ? 'border-brand-400 bg-brand-50 text-brand-700' : 'border-line bg-card text-ink-600',
          )}
        >
          <Icon name="filter-funnel" size={14} />
          {t('筛选')}{activeCount > 0 ? ` · ${activeCount}` : ''}
          <Icon name="chevron-down" size={12} className={cn('transition-transform duration-200', mobileOpen && 'rotate-180')} />
        </button>
        <CountNote total={total} filtered={filtered} />
      </div>

      {/* 过滤面板 */}
      <div className={cn('mt-3 md:mt-0', mobileOpen ? 'block' : 'hidden md:block')}>
        <div className="flex flex-wrap items-center gap-x-3 gap-y-2.5 rounded-lg border border-line bg-card-warm/70 px-3.5 py-3 md:rounded-none md:border-0 md:bg-transparent md:px-0 md:py-0">
          {controls}
          <div className="ml-auto hidden items-center gap-3 md:flex">
            <CountNote total={total} filtered={filtered} />
          </div>
        </div>
      </div>

      {/* 激活的主题过滤 chip（热点带带入） */}
      {filters.themeId && (
        <motion.div
          initial={{ scale: 0.9, opacity: 0 }}
          animate={{ scale: 1, opacity: 1 }}
          transition={{ type: 'spring', stiffness: 520, damping: 32 }}
          className="mt-2.5 flex items-center gap-2"
        >
          <span className="inline-flex items-center gap-1.5 rounded-pill bg-brand-100 px-2.5 py-1 text-caption font-medium text-brand-700">
            <Icon name="flame-line" size={12} />
            {t('主题：')}{catalystsContract.themeName(filters.themeId)}
            <button onClick={() => set({ themeId: null })} aria-label={t("清除主题过滤")} className="rounded-full p-0.5 hover:bg-brand-100">
              <Icon name="x" size={11} />
            </button>
          </span>
        </motion.div>
      )}
    </div>
  );
}

function CountNote({ total, filtered }: { total: number | null; filtered: boolean }) {
  return (
    <p className="font-mono text-micro text-ink-400 tnum">
      {total === null ? '—' : t('{n} 条', { n: total })}
      {filtered && total !== null && <span className="text-ink-300"> {t('· 已过滤')}</span>}
    </p>
  );
}
