/**
 * B1 筛选工作台（screener.md）
 * 常驻：分档 / 周期 / 偏好 / 返回数量 / 扫描
 * 更多筛选：预设、板块、价格与成交额；折叠时仍展示当前约束
 * 行 stagger 60ms；过滤器变更主按钮脉冲（box-shadow 呼吸 1.2s ×2）
 */
import SoftBadge from '@/components/shared/SoftBadge';
import { useState } from 'react';
import { motion } from 'framer-motion';
import type { SectorOption, StrengthProfile } from '@/api/types';
import { cn } from '@/lib/utils';
import Icon from '@/components/icons';
import Segmented from '@/components/shared/Segmented';
import FilterButton from '@/components/shared/FilterButton';
import SelectionViewport from '@/components/shared/SelectionViewport';
import MenuSelect from '@/components/shared/MenuSelect';
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
import { t as __t } from '../../i18n/core.ts';

const EASE_PAPER = [0.16, 1, 0.3, 1] as [number, number, number, number];

/* ---------------- 分档 Segmented（共享件 + Mono 11 数量徽标） ---------------- */
const TIER_OPTIONS: { value: TierFilter; label: string }[] = [
  { value: 'all', label: __t('全部') },
  { value: 'S', label: 'S' },
  { value: 'A', label: 'A' },
  { value: 'B', label: 'B' },
  { value: 'C', label: 'C' },
];

function TierSegmented({
  value,
  counts,
  coversPool,
  onChange,
}: {
  value: TierFilter;
  counts: Record<TierFilter, number>;
  /** 计数覆盖整个候选池时不加限定语；否则明确它只描述当前快照的行。 */
  coversPool: boolean;
  onChange: (v: TierFilter) => void;
}) {
  const scopeNote = coversPool ? __t('已评分候选池') : __t('当前快照返回的行');
  /* 只剩三处真实差异：徽标标签、aria/title 文案、可横向滚动（滚动条投影用
     layoutScroll）。键盘/结构/指示器全部复用共享件——两份抄写在本 PR 里已经
     各自跑偏过一次（审计 2.5.9）。 */
  return (
    <Segmented<TierFilter>
      options={TIER_OPTIONS}
      value={value}
      onChange={onChange}
      scrollable
      ariaLabel={__t('强度分档 · 计数基于{scope}', { scope: scopeNote })}
      title={__t('分档计数基于{scope}', { scope: scopeNote })}
      renderLabel={(o, active) => (
        <span className="flex items-center gap-1.5">
          {o.label}
          <span className={cn('min-w-4 rounded-[5px] px-1 py-px font-mono text-[11px] leading-[14px] tnum', active ? 'bg-paper-2 text-ink-600' : 'text-ink-400')}>
            {counts[o.value]}
          </span>
        </span>
      )}
    />
  );
}

/* ---------------- 小件：字段标签 ---------------- */
function FieldLabel({ children }: { children: string }) {
  return <p className="mb-2 text-caption font-medium text-ink-500">{children}</p>;
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
  // 外部值变化（如 chip 移除）时在渲染期同步本地输入（React 推荐模式）。
  // Object.is：NaN !== NaN 恒真，一旦上游漏进 NaN，普通比较会让渲染期
  // setState 永不收敛，直接撞 React 重渲染上限把整页打崩。
  const [prevValue, setPrevValue] = useState(value);
  if (!Object.is(prevValue, value)) {
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
          // 裸 `.`（想输 .5 的中间态）Number() 得 NaN：输入框保留文本，
          // 但绝不把非有限值交给上游筛选状态。
          const n = Number(t);
          onCommit(t === '' || !Number.isFinite(n) ? null : n);
        }}
        className="screener-price-input h-8 w-[88px] rounded-[9px] border border-line/70 bg-paper-2/50 pl-6 pr-2 font-mono text-caption text-ink-800 tnum placeholder:text-ink-300 hover:border-line-strong focus-visible:border-brand-400"
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
  /* 基础立体影 = tailwind shadow-btn-hi。framer 的 dirty 脉冲环走内联 boxShadow，
     会压掉 class 阴影，只能把基础影并进每一帧动画值（各帧层数一致才可补间）。 */
  const base = 'inset 0 1px 0 rgba(255,255,255,.12), 0 1px 2px rgba(16,24,40,.12), 0 3px 7px -4px rgba(16,24,40,.24)';
  return (
    <motion.button
      onClick={onScan}
      disabled={scanning}
      animate={
        dirty && !scanning
          ? { boxShadow: [`${base}, 0 0 0 0 rgba(46,70,224,.38)`, `${base}, 0 0 0 9px rgba(46,70,224,0)`, `${base}, 0 0 0 0 rgba(46,70,224,0)`] }
          : { boxShadow: `${base}, 0 0 0 0 rgba(46,70,224,0)` }
      }
      transition={dirty && !scanning ? { duration: 1.2, repeat: 2 } : { duration: 0.16 }}
      className={cn(
        'scan-trigger relative h-9 min-w-[168px] overflow-hidden rounded-[9px] bg-brand-600 px-4 text-white shadow-btn-hi transition-[filter] duration-fast',
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
            <span className="text-body-s font-medium">{__t('扫描中 · 等待后台结果')}</span>
          </>
        ) : (
          <>
            <Icon name="crosshair" size={16} />
            <span className="text-body-s font-medium">{__t('开始扫描')}</span>
            <span className="font-mono text-micro text-white/70 tnum">≈{universeCount} {__t('只')}</span>
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
  universe: {
    tierCounts: Record<TierFilter, number>;
    /** 计数是否覆盖整个候选池；false 时只描述当前快照返回的行（审计 P2-10）。 */
    tierCountsCoverPool: boolean;
    sectors: string[];
    count: number;
  };
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

  const selectedPreset = presets?.find((preset) => preset.id === draft.presetId);
  const selectedSectors = draft.sectors.map((id) => sectorOptions.find((sector) => sector.id === id)?.name ?? id);
  const priceSummary = draft.priceMin !== null && draft.priceMax !== null
    ? `${__t('价格区间')} $${draft.priceMin} – $${draft.priceMax}`
    : draft.priceMin !== null
      ? `${__t('价格区间')} ≥ $${draft.priceMin}`
      : draft.priceMax !== null ? `${__t('价格区间')} ≤ $${draft.priceMax}` : null;
  const volumeSummary = draft.minDollarVol > 0
    ? `${__t('成交额下限')} ${DOLLAR_VOL_OPTIONS.find((option) => option.value === draft.minDollarVol)?.label ?? draft.minDollarVol}`
    : null;
  const advancedSummary = [
    selectedPreset?.name,
    selectedSectors.length > 0 ? `${selectedSectors.slice(0, 2).join(' / ')}${selectedSectors.length > 2 ? ` +${selectedSectors.length - 2}` : ''}` : null,
    priceSummary,
    volumeSummary,
  ].filter((value): value is string => Boolean(value));

  return (
    <motion.section
      initial="hidden"
      animate="show"
      variants={{ show: { transition: { staggerChildren: 0.06 } } }}
      className="card-surface p-4 sm:p-5"
      aria-label={__t("筛选工作台")}
      data-testid="screener-filter-workbench"
    >
      <motion.div variants={row} className="flex min-w-0 flex-wrap items-end gap-x-5 gap-y-4">
        <div className="w-full min-w-0 sm:w-auto">
          <FieldLabel>{__t('强度分档')}</FieldLabel>
          <TierSegmented
            value={draft.tier}
            counts={universe.tierCounts}
            coversPool={universe.tierCountsCoverPool}
            onChange={(tier) => patch({ tier, presetId: null, minScore: null })}
          />
        </div>
        <div className="w-full min-w-0 sm:w-auto">
          <FieldLabel>{__t('周期')}</FieldLabel>
          <Segmented<Timeframe>
            options={(['short', 'mid', 'long', 'all'] as const).map((v) => ({ value: v, label: TIMEFRAME_CN[v] }))}
            value={draft.timeframe}
            onChange={(timeframe) => patch({ timeframe })}
            ariaLabel={__t('周期')}
          />
        </div>
        <div className="min-w-0">
          <FieldLabel>{__t('偏好')}</FieldLabel>
          <Segmented<ProfilePref>
            options={(['conservative', 'balanced', 'aggressive'] as const).map((v) => ({ value: v, label: PROFILE_CN[v] }))}
            value={draft.profile}
            onChange={(profile) => patch({ profile, presetId: null })}
            ariaLabel={__t('偏好')}
          />
        </div>
        <div>
          <FieldLabel>{__t('返回数量')}</FieldLabel>
          <MenuSelect ariaLabel={__t("返回数量 Top N")} value={draft.topN} onChange={(topN) => patch({ topN })} options={TOPN_OPTIONS} />
        </div>
        <ScanButton scanning={scanning} dirty={dirty} universeCount={universe.count} onScan={onScan} className="w-full sm:ml-auto sm:w-auto" />
      </motion.div>

      {/* 次要条件收纳；已选择的范围常驻，避免折叠后忘记当前扫描门槛。 */}
      <details className="group/filters mt-5 border-t border-line/70 pt-3" data-testid="screener-advanced-filters">
        <summary className="disclosure-trigger flex cursor-pointer list-none flex-wrap items-center gap-x-3 gap-y-2 rounded-lg py-1 text-caption text-ink-500 outline-none transition-colors hover:text-ink-800 focus-visible:ring-2 focus-visible:ring-brand-400/40 [&::-webkit-details-marker]:hidden">
          <span className="inline-flex shrink-0 items-center gap-2 font-medium text-ink-700">
            <Icon name="filter-funnel" size={14} className="text-ink-400" />
            {__t('更多筛选')}
            <Icon name="chevron-down" size={13} className="text-ink-400 transition-transform group-open/filters:rotate-180" />
          </span>
          <span className="flex min-w-0 flex-1 flex-wrap items-center gap-x-2 gap-y-1 text-micro text-ink-500" data-testid="screener-advanced-summary">
            {advancedSummary.length > 0 ? advancedSummary.map((label, index) => (
              <SoftBadge key={index}>{label}</SoftBadge>
            )) : <span>{__t('预设策略')} · {__t('板块（多选）')} · {__t('价格区间')} · {__t('成交额下限')}</span>}
          </span>
        </summary>

        <div className="space-y-4 pt-4">
          <div className="min-w-0">
            <FieldLabel>{__t('预设策略')}</FieldLabel>
            {presetsFailed ? (
              <p className="flex h-8 items-center text-caption text-ink-400">{__t('预设暂不可用 · 使用默认分档')}</p>
            ) : presets === null ? (
              <div className="flex gap-2" aria-hidden="true">
                {Array.from({ length: 3 }, (_, i) => (
                  <span key={i} className="skeleton-shimmer h-8 w-20 rounded-md" />
                ))}
              </div>
            ) : (
              <SelectionViewport>
              <div className="mobile-selection-rail flex flex-wrap gap-2">
                {presets.map((preset) => {
                  const active = draft.presetId === preset.id;
                  return (
                    <FilterButton
                      key={preset.id}
                      onClick={() => applyPreset(preset.id)}
                      title={preset.description}
                      active={active}
                    >
                      <Icon name="spark-ai" size={13} className={active ? 'text-brand-600' : 'text-ink-400'} />
                      {preset.name}
                    </FilterButton>
                  );
                })}
              </div>
              </SelectionViewport>
            )}
          </div>
          <div data-screener-field="sectors" className="min-w-0">
            <FieldLabel>{__t('板块（多选）')}</FieldLabel>
            {sectorOptions.length === 0 ? (
              <div className="flex flex-wrap gap-2" aria-hidden="true">
                {Array.from({ length: 5 }, (_, i) => (
                  <span key={i} className="skeleton-shimmer h-7 w-16 rounded-md" />
                ))}
              </div>
            ) : (
              <SelectionViewport>
              <div className="mobile-selection-rail flex flex-wrap items-center gap-1.5">
                {visibleSectors.map((sector) => (
                  <FilterButton key={sector.id} onClick={() => toggleSector(sector.id)} active={draft.sectors.includes(sector.id)} className="shrink-0">
                    {sector.name}
                  </FilterButton>
                ))}
                {hiddenCount > 0 && (
                  <button
                    type="button"
                    onClick={() => setShowAllSectors(true)}
                    className="flex h-8 shrink-0 items-center whitespace-nowrap rounded-lg bg-paper-2 px-2.5 font-mono text-caption text-ink-500 tnum transition-colors hover:bg-paper hover:text-ink-800"
                  >
                    +{hiddenCount}
                  </button>
                )}
                {showAllSectors && sectorOptions.length > SECTOR_COLLAPSE_AT && (
                  <button type="button" onClick={() => setShowAllSectors(false)} className="flex h-8 shrink-0 items-center whitespace-nowrap rounded-lg px-2 text-caption text-ink-400 transition-colors hover:text-ink-600">
                    {__t('收起')}
                  </button>
                )}
              </div>
              </SelectionViewport>
            )}
          </div>
          <div className="flex flex-wrap items-end gap-x-6 gap-y-4 border-t border-line/60 pt-4">
            <div data-screener-field="price">
              <FieldLabel>{__t('价格区间')}</FieldLabel>
              <div className="flex items-center gap-1.5">
                <PriceInput value={draft.priceMin} placeholder={__t("最低")} ariaLabel={__t("最低价格")} onCommit={(priceMin) => patch({ priceMin })} />
                <span className="text-ink-300" aria-hidden="true">–</span>
                <PriceInput value={draft.priceMax} placeholder={__t("最高")} ariaLabel={__t("最高价格")} onCommit={(priceMax) => patch({ priceMax })} />
              </div>
            </div>
            <div data-screener-field="dollar-volume">
              <FieldLabel>{__t('成交额下限')}</FieldLabel>
              <MenuSelect ariaLabel={__t("成交额下限")} value={draft.minDollarVol} onChange={(minDollarVol) => patch({ minDollarVol })} options={DOLLAR_VOL_OPTIONS} />
            </div>
          </div>
        </div>
      </details>
    </motion.section>
  );
}
