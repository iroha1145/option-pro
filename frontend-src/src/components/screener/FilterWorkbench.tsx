/**
 * B1 筛选工作台（screener.md）
 * 行1 分档 Segmented（带数量徽标）+ 预设策略按钮（浅品牌底选中）
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
import FilterButton from '@/components/shared/FilterButton';
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
        <span className="flex items-baseline gap-1">
          {o.label}
          <span className={cn('font-mono text-[11px] leading-[14px] tnum', active ? 'text-brand-600' : 'text-ink-300')}>
            {counts[o.value]}
          </span>
        </span>
      )}
    />
  );
}

/* ---------------- 小件：字段标签 ---------------- */
function FieldLabel({ children }: { children: string }) {
  return <p className="mb-1.5 text-micro font-medium uppercase tracking-[0.08em] text-ink-400">{children}</p>;
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
  /* 基础立体影 = tailwind shadow-btn-hi。framer 的 dirty 脉冲环走内联 boxShadow，
     会压掉 class 阴影，只能把基础影并进每一帧动画值（各帧层数一致才可补间）。 */
  const base = 'inset 0 1px 0 rgba(255,255,255,.16), 0 1px 2px rgba(16,24,40,.18), 0 4px 12px -4px rgba(16,24,40,.34)';
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
        'relative h-10 min-w-[168px] overflow-hidden rounded-md bg-brand-600 px-4 text-white shadow-btn-hi transition-[filter] duration-fast',
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

  return (
    <motion.section
      initial="hidden"
      animate="show"
      variants={{ show: { transition: { staggerChildren: 0.06 } } }}
      className="card-surface p-4 sm:p-5"
      aria-label={__t("筛选工作台")}
    >
      {/* 行 1 · 分档与预设 */}
      <motion.div variants={row} className="flex min-w-0 flex-wrap items-center gap-x-5 gap-y-3">
        <div className="w-full min-w-0 sm:w-auto">
          <FieldLabel>{__t('强度分档')}</FieldLabel>
          <TierSegmented
            value={draft.tier}
            counts={universe.tierCounts}
            coversPool={universe.tierCountsCoverPool}
            onChange={(tier) => patch({ tier, presetId: null, minScore: null })}
          />
        </div>
        <div className="hidden h-9 w-px bg-line sm:block" aria-hidden="true" />
        <div className="w-full min-w-0 sm:w-auto sm:flex-1">
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
            <div className="flex flex-wrap gap-2">
              {presets.map((p) => {
                const active = draft.presetId === p.id;
                return (
                  <FilterButton
                    key={p.id}
                    onClick={() => applyPreset(p.id)}
                    title={p.description}
                    active={active}
                  >
                    <Icon name="spark-ai" size={13} className={active ? 'text-brand-600' : 'text-ink-300'} />
                    {p.name}
                  </FilterButton>
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
          <FieldLabel>{__t('周期')}</FieldLabel>
          <Segmented<Timeframe>
            options={(['short', 'mid', 'long', 'all'] as const).map((v) => ({ value: v, label: TIMEFRAME_CN[v] }))}
            value={draft.timeframe}
            onChange={(timeframe) => patch({ timeframe })}
          />
        </div>
        <div>
          <FieldLabel>{__t('偏好')}</FieldLabel>
          <Segmented<ProfilePref>
            options={(['conservative', 'balanced', 'aggressive'] as const).map((v) => ({ value: v, label: PROFILE_CN[v] }))}
            value={draft.profile}
            onChange={(profile) => patch({ profile, presetId: null })}
          />
        </div>
        <div>
          <FieldLabel>{__t('返回数量')}</FieldLabel>
          <MenuSelect ariaLabel={__t("返回数量 Top N")} value={draft.topN} onChange={(topN) => patch({ topN })} options={TOPN_OPTIONS} />
        </div>
      </motion.div>

      <div className="my-4 h-px bg-line" aria-hidden="true" />

      {/* 行 3 · 板块 / 价格 / 成交额 / 扫描钮 */}
      <motion.div variants={row} className="flex flex-wrap items-end gap-x-3 gap-y-3 sm:gap-x-6">
        <div
          data-screener-field="sectors"
          className="w-full min-w-0 flex-none sm:w-auto sm:flex-1"
        >
          <FieldLabel>{__t('板块（多选）')}</FieldLabel>
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
                  <FilterButton
                    key={s.id}
                    onClick={() => toggleSector(s.id)}
                    active={active}
                    className="shrink-0"
                  >
                    {s.name}
                  </FilterButton>
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
                  {__t('收起')}
                </button>
              )}
            </div>
          )}
        </div>
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
          <MenuSelect
            ariaLabel={__t("成交额下限")}
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
