/**
 * B1 筛选工作台（screener.md）
 * 行1 分档 Segmented（带数量徽标）+ 预设策略 chips（spring-pop 1.04）
 * 行2 周期 / 偏好 / Top N
 * 行3 板块多选（折叠 +N）· 价格区间 · 成交额下限 · 开始扫描（真实等待态）
 * 行 stagger 60ms；过滤器变更主按钮脉冲（box-shadow 呼吸 1.2s ×2）
 */
import { useState } from 'react';
import { motion } from 'framer-motion';
import type { SectorOption, StrengthProfile } from '@/api/types';
import { cn } from '@/lib/utils';
import Icon from '@/components/icons';
import Segmented from '@/components/shared/Segmented';
import {
  DOLLAR_VOL_OPTIONS,
  PROFILE_CN,
  TIMEFRAME_CN,
  TOPN_OPTIONS,
  type ProfilePref,
  type ScanFilters,
  type TierFilter,
  type Timeframe,
} from './types';

const EASE_PAPER = [0.16, 1, 0.3, 1] as [number, number, number, number];
const SPRING_POP = { type: 'spring', stiffness: 520, damping: 32 } as const;

/* ---------------- 分档 Segmented（自定义：带 Mono 11 数量徽标） ---------------- */
const TIER_OPTIONS: { value: TierFilter; label: string }[] = [
  { value: 'all', label: '全部' },
  { value: 'S', label: 'S' },
  { value: 'A', label: 'A' },
  { value: 'B', label: 'B' },
  { value: 'C', label: 'C' },
];

function TierSegmented({
  value,
  counts,
  onChange,
}: {
  value: TierFilter;
  counts: Record<TierFilter, number>;
  onChange: (v: TierFilter) => void;
}) {
  return (
    <div role="tablist" aria-label="强度分档" className="no-scrollbar inline-flex max-w-full items-center gap-0.5 overflow-x-auto rounded-md border border-line bg-card-warm p-0.5">
      {TIER_OPTIONS.map((o) => {
        const active = value === o.value;
        return (
          <button
            key={o.value}
            role="tab"
            aria-selected={active}
            onClick={() => onChange(o.value)}
            className={cn(
              'relative shrink-0 whitespace-nowrap rounded-[6px] px-2.5 py-1 text-caption font-medium transition-colors duration-fast',
              active ? 'text-ink-800' : 'text-ink-400 hover:text-ink-600',
            )}
          >
            {active && (
              <motion.span
                layoutId="tier-thumb"
                className="absolute inset-0 rounded-[6px] bg-card shadow-sh-1"
                transition={{ duration: 0.26, ease: EASE_PAPER }}
                aria-hidden="true"
              />
            )}
            <span className="relative z-10 flex items-baseline gap-1">
              {o.label}
              <span className={cn('font-mono text-[11px] leading-[14px] tnum', active ? 'text-brand-600' : 'text-ink-300')}>
                {counts[o.value]}
              </span>
            </span>
          </button>
        );
      })}
    </div>
  );
}

/* ---------------- 小件：字段标签 ---------------- */
function FieldLabel({ children }: { children: string }) {
  return <p className="mb-1.5 text-micro font-medium uppercase tracking-[0.08em] text-ink-400">{children}</p>;
}

/* ---------------- 小件：下拉选择（发丝边框 r-md） ---------------- */
function SelectField({
  value,
  onChange,
  options,
  ariaLabel,
}: {
  value: number;
  onChange: (v: number) => void;
  options: { value: number; label: string }[];
  ariaLabel: string;
}) {
  return (
    <div className="relative">
      <select
        aria-label={ariaLabel}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        className="h-8 appearance-none rounded-md border border-line bg-card pl-2.5 pr-7 font-mono text-caption text-ink-600 tnum transition-colors hover:border-line-strong focus-visible:border-brand-400"
      >
        {options.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
      <Icon name="chevron-down" size={12} className="pointer-events-none absolute right-2 top-1/2 -translate-y-1/2 text-ink-400" />
    </div>
  );
}

/* ---------------- 价格区间输入（Mono，$ 前缀） ---------------- */
function PriceInput({
  value,
  placeholder,
  ariaLabel,
  onCommit,
}: {
  value: number | null;
  placeholder: string;
  ariaLabel: string;
  onCommit: (v: number | null) => void;
}) {
  const [text, setText] = useState(value === null ? '' : String(value));
  // 外部值变化（如 chip 移除）时在渲染期同步本地输入（React 推荐模式）
  const [prevValue, setPrevValue] = useState(value);
  if (prevValue !== value) {
    setPrevValue(value);
    setText(value === null ? '' : String(value));
  }
  return (
    <div className="relative">
      <span className="pointer-events-none absolute left-2 top-1/2 -translate-y-1/2 font-mono text-caption text-ink-300">$</span>
      <input
        value={text}
        inputMode="decimal"
        aria-label={ariaLabel}
        placeholder={placeholder}
        onChange={(e) => {
          const t = e.target.value;
          if (!/^\d*\.?\d*$/.test(t)) return;
          setText(t);
          onCommit(t === '' ? null : Number(t));
        }}
        className="h-8 w-[88px] rounded-md border border-line bg-card pl-6 pr-2 font-mono text-caption text-ink-800 tnum placeholder:text-ink-300 hover:border-line-strong focus-visible:border-brand-400"
      />
    </div>
  );
}

/* ---------------- 扫描按钮（无真实计数时只显示不定等待态） ---------------- */
export function ScanButton({
  scanning,
  dirty,
  universeCount,
  onScan,
  className,
}: {
  scanning: boolean;
  dirty: boolean;
  universeCount: number;
  onScan: () => void;
  className?: string;
}) {
  return (
    <motion.button
      onClick={onScan}
      disabled={scanning}
      animate={
        dirty && !scanning
          ? { boxShadow: ['0 0 0 0 rgba(46,70,224,.38)', '0 0 0 9px rgba(46,70,224,0)', '0 0 0 0 rgba(46,70,224,0)'] }
          : { boxShadow: '0 0 0 0 rgba(46,70,224,0)' }
      }
      transition={dirty && !scanning ? { duration: 1.2, repeat: 2 } : { duration: 0.16 }}
      className={cn(
        'relative h-10 min-w-[168px] overflow-hidden rounded-md bg-brand-600 px-4 text-white transition-[filter] duration-fast',
        scanning ? 'cursor-wait' : 'hover:brightness-105',
        className,
      )}
      aria-live="polite"
      aria-busy={scanning}
    >
      {scanning && (
        <motion.span
          className="absolute inset-y-0 w-2/5 bg-gradient-to-r from-transparent via-white/18 to-transparent"
          initial={{ x: '-120%' }}
          animate={{ x: '350%' }}
          transition={{ duration: 1.25, ease: 'linear', repeat: Infinity }}
          aria-hidden="true"
        />
      )}
      <span className="relative z-10 flex items-center justify-center gap-2">
        {scanning ? (
          <>
            <span className="size-[18px] animate-spin rounded-full border-2 border-white/35 border-t-white" aria-hidden="true" />
            <span className="text-body-s font-medium">扫描中 · 等待后台结果</span>
          </>
        ) : (
          <>
            <Icon name="crosshair" size={16} />
            <span className="text-body-s font-medium">开始扫描</span>
            <span className="font-mono text-micro text-white/70 tnum">≈{universeCount} 只</span>
          </>
        )}
      </span>
    </motion.button>
  );
}

/* ================= 工作台主体 ================= */
interface FilterWorkbenchProps {
  draft: ScanFilters;
  onChange: (f: ScanFilters) => void;
  universe: { tierCounts: Record<TierFilter, number>; sectors: string[]; count: number };
  /** 板块选项：live 来自 /strength/profiles sectors（id+中文名，下发 id）；mock 回退扫描行 sector 名（id=name） */
  sectorOptions: SectorOption[];
  presets: StrengthProfile[] | null;
  presetsFailed: boolean;
  scanning: boolean;
  dirty: boolean;
  onScan: () => void;
}

const SECTOR_COLLAPSE_AT = 6;

export default function FilterWorkbench({
  draft,
  onChange,
  universe,
  sectorOptions,
  presets,
  presetsFailed,
  scanning,
  dirty,
  onScan,
}: FilterWorkbenchProps) {
  const [showAllSectors, setShowAllSectors] = useState(false);
  const visibleSectors = showAllSectors ? sectorOptions : sectorOptions.slice(0, SECTOR_COLLAPSE_AT);
  const hiddenCount = sectorOptions.length - visibleSectors.length;

  const patch = (p: Partial<ScanFilters>) => onChange({ ...draft, ...p });

  const toggleSector = (id: string) => {
    const has = draft.sectors.includes(id);
    patch({ sectors: has ? draft.sectors.filter((x) => x !== id) : [...draft.sectors, id] });
  };

  const applyPreset = (id: string) => {
    if (draft.presetId === id) {
      patch({ presetId: null, minScore: null });
      return;
    }
    // 契约 /strength/profiles 枚举（conservative/balanced/aggressive）→ 直接落偏好
    if (id === 'conservative' || id === 'balanced' || id === 'aggressive') {
      patch({ presetId: id, profile: id, minScore: null });
      return;
    }
    // mock 预设策略 → 偏好映射 + 强度下限
    if (id === 'breakout') patch({ presetId: id, profile: 'aggressive', minScore: 70 });
    else if (id === 'lowvol') patch({ presetId: id, profile: 'conservative', minScore: null });
    else patch({ presetId: id, profile: 'balanced', minScore: null });
  };

  const row = {
    hidden: { opacity: 0, y: 14 },
    show: { opacity: 1, y: 0, transition: { duration: 0.48, ease: EASE_PAPER } },
  };

  return (
    <motion.section
      initial="hidden"
      animate="show"
      variants={{ show: { transition: { staggerChildren: 0.06 } } }}
      className="card-surface p-4 sm:p-5"
      aria-label="筛选工作台"
    >
      {/* 行 1 · 分档与预设 */}
      <motion.div variants={row} className="flex min-w-0 flex-wrap items-center gap-x-5 gap-y-3">
        <div className="w-full min-w-0 sm:w-auto">
          <FieldLabel>强度分档</FieldLabel>
          <TierSegmented
            value={draft.tier}
            counts={universe.tierCounts}
            onChange={(tier) => patch({ tier, presetId: null, minScore: null })}
          />
        </div>
        <div className="hidden h-9 w-px bg-line sm:block" aria-hidden="true" />
        <div className="w-full min-w-0 sm:w-auto sm:flex-1">
          <FieldLabel>预设策略</FieldLabel>
          {presetsFailed ? (
            <p className="flex h-8 items-center text-caption text-ink-400">预设不可用 · 已降级为静态分档</p>
          ) : presets === null ? (
            <div className="flex gap-2" aria-hidden="true">
              {Array.from({ length: 3 }, (_, i) => (
                <span key={i} className="skeleton-shimmer h-8 w-20 rounded-pill" />
              ))}
            </div>
          ) : (
            <div className="flex flex-wrap gap-2">
              {presets.map((p) => {
                const active = draft.presetId === p.id;
                return (
                  <motion.button
                    key={p.id}
                    onClick={() => applyPreset(p.id)}
                    animate={{ scale: active ? 1.04 : 1 }}
                    transition={SPRING_POP}
                    title={p.description}
                    aria-pressed={active}
                    className={cn(
                      'flex h-8 items-center gap-1.5 rounded-pill border px-3 text-caption transition-colors duration-fast',
                      active
                        ? 'border-brand-400 bg-brand-100 text-brand-700'
                        : 'border-line bg-card text-ink-500 hover:border-brand-400/60 hover:text-brand-600',
                    )}
                  >
                    <Icon name="spark-ai" size={13} className={active ? 'text-brand-600' : 'text-ink-300'} />
                    {p.name}
                  </motion.button>
                );
              })}
            </div>
          )}
        </div>
      </motion.div>

      <div className="my-4 h-px bg-line" aria-hidden="true" />

      {/* 行 2 · 周期 / 偏好 / Top N */}
      <motion.div variants={row} className="flex flex-wrap items-end gap-x-6 gap-y-3">
        <div>
          <FieldLabel>周期</FieldLabel>
          <Segmented<Timeframe>
            options={(['short', 'mid', 'long', 'all'] as const).map((v) => ({ value: v, label: TIMEFRAME_CN[v] }))}
            value={draft.timeframe}
            onChange={(timeframe) => patch({ timeframe })}
          />
        </div>
        <div>
          <FieldLabel>偏好</FieldLabel>
          <Segmented<ProfilePref>
            options={(['conservative', 'balanced', 'aggressive'] as const).map((v) => ({ value: v, label: PROFILE_CN[v] }))}
            value={draft.profile}
            onChange={(profile) => patch({ profile, presetId: null })}
          />
        </div>
        <div>
          <FieldLabel>返回数量</FieldLabel>
          <SelectField ariaLabel="返回数量 Top N" value={draft.topN} onChange={(topN) => patch({ topN })} options={TOPN_OPTIONS} />
        </div>
      </motion.div>

      <div className="my-4 h-px bg-line" aria-hidden="true" />

      {/* 行 3 · 板块 / 价格 / 成交额 / 扫描钮 */}
      <motion.div variants={row} className="flex flex-wrap items-end gap-x-3 gap-y-3 sm:gap-x-6">
        <div
          data-screener-field="sectors"
          className="w-full min-w-0 flex-none sm:w-auto sm:flex-1"
        >
          <FieldLabel>板块（多选）</FieldLabel>
          {sectorOptions.length === 0 ? (
            <div className="flex flex-wrap gap-2" aria-hidden="true">
              {Array.from({ length: 5 }, (_, i) => (
                <span key={i} className="skeleton-shimmer h-7 w-16 rounded-xs" />
              ))}
            </div>
          ) : (
            <div className="flex flex-wrap items-center gap-1.5">
              {visibleSectors.map((s) => {
                const active = draft.sectors.includes(s.id);
                return (
                  <motion.button
                    key={s.id}
                    onClick={() => toggleSector(s.id)}
                    animate={{ scale: active ? 1.04 : 1 }}
                    transition={SPRING_POP}
                    aria-pressed={active}
                    className={cn(
                      'flex h-7 shrink-0 items-center whitespace-nowrap rounded-xs border px-2 text-caption transition-colors duration-fast',
                      active
                        ? 'border-brand-400 bg-brand-100 text-brand-700'
                        : 'border-line bg-card text-ink-500 hover:border-brand-400/60 hover:text-brand-600',
                    )}
                  >
                    {s.name}
                  </motion.button>
                );
              })}
              {hiddenCount > 0 && (
                <button
                  onClick={() => setShowAllSectors(true)}
                  className="flex h-7 shrink-0 items-center whitespace-nowrap rounded-xs border border-dashed border-line-strong px-2 font-mono text-caption text-ink-400 tnum transition-colors hover:text-brand-600"
                >
                  +{hiddenCount}
                </button>
              )}
              {showAllSectors && sectorOptions.length > SECTOR_COLLAPSE_AT && (
                <button
                  onClick={() => setShowAllSectors(false)}
                  className="flex h-7 shrink-0 items-center whitespace-nowrap rounded-xs px-1.5 text-caption text-ink-400 transition-colors hover:text-ink-600"
                >
                  收起
                </button>
              )}
            </div>
          )}
        </div>
        <div data-screener-field="price">
          <FieldLabel>价格区间</FieldLabel>
          <div className="flex items-center gap-1.5">
            <PriceInput value={draft.priceMin} placeholder="最低" ariaLabel="最低价格" onCommit={(priceMin) => patch({ priceMin })} />
            <span className="text-ink-300" aria-hidden="true">–</span>
            <PriceInput value={draft.priceMax} placeholder="最高" ariaLabel="最高价格" onCommit={(priceMax) => patch({ priceMax })} />
          </div>
        </div>
        <div data-screener-field="dollar-volume">
          <FieldLabel>成交额下限</FieldLabel>
          <SelectField
            ariaLabel="成交额下限"
            value={draft.minDollarVol}
            onChange={(minDollarVol) => patch({ minDollarVol })}
            options={DOLLAR_VOL_OPTIONS}
          />
        </div>
        <ScanButton scanning={scanning} dirty={dirty} universeCount={universe.count} onScan={onScan} className="ml-auto" />
      </motion.div>
    </motion.section>
  );
}
