/**
 * B4 右栏（sectors.md：右 5 列，三张卡）
 * 1. IV 热力卡：9 宫格代码砖（IV 关注度最高 8 只 +「+」虚线砖 → 命令面板）
 *    砖色按 IV rank 低→高（up→brand→down §1.7），砖内代码 Mono 600 + rank% Micro
 * 2. 波动率洞察卡（brand-50 底，spark-ai ai-600）：编辑式短文，关键词 up-700 加粗
 * 3. 板块相关性卡：对比标普 500 / 纳指 100 / 美债 20Y，Mono 系数 + 微条（-1…+1，中轴发丝线）
 */
import { useMemo } from 'react';
import { motion } from 'framer-motion';
import { cn } from '@/lib/utils';
import Icon from '@/components/icons';
import { SkeletonBlock, SkeletonCard } from '@/components/shared/Skeleton';
import type { IvRowVm, SectorVm } from './model';
import { ivRankColor, toneOnColor } from './model';

/* ---------- 1. IV 热力卡 ---------- */
function IvHeatCard({
  rows,
  loading,
  onOpenTicker,
  onOpenPalette,
}: {
  rows: IvRowVm[];
  loading: boolean;
  onOpenTicker: (t: string) => void;
  onOpenPalette: () => void;
}) {
  const top = useMemo(
    () => rows.filter((r) => r.rank !== null).sort((a, b) => (b.rank ?? 0) - (a.rank ?? 0)).slice(0, 8),
    [rows],
  );
  return (
    <div className="card-surface p-5">
      <div className="flex items-center justify-between">
        <p className="eyebrow">IV 热力 · 关注</p>
        <Icon name="flame-line" size={16} className="text-ink-400" />
      </div>
      <div className="mt-4 grid grid-cols-3 gap-2">
        {loading
          ? Array.from({ length: 8 }, (_, i) => <SkeletonBlock key={i} className="h-14 rounded-md" />)
          : top.map((r, i) => {
              const bg = ivRankColor(r.rank ?? 0);
              const light = toneOnColor(bg) === 'light';
              return (
                <motion.button
                  key={r.ticker}
                  type="button"
                  initial={{ opacity: 0, y: 10 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{ duration: 0.4, ease: [0.16, 1, 0.3, 1], delay: i * 0.04 }}
                  whileHover={{ y: -2, transition: { duration: 0.16, ease: [0.22, 1, 0.36, 1] } }}
                  onClick={() => onOpenTicker(r.ticker)}
                  aria-label={`${r.ticker} IV rank ${r.rank}，打开详情`}
                  className="flex h-14 flex-col items-center justify-center gap-0.5 rounded-md shadow-sh-1 transition-shadow duration-fast hover:shadow-sh-2"
                  style={{ backgroundColor: bg }}
                >
                  <span className={cn('font-mono text-caption font-semibold leading-none', light ? 'text-white' : 'text-ink-800')}>
                    {r.ticker}
                  </span>
                  <span className={cn('font-mono text-micro leading-none tnum', light ? 'text-white/75' : 'text-ink-500')}>
                    {r.rank}%
                  </span>
                </motion.button>
              );
            })}
        {/* 「+」添加砖 → 命令面板预选 */}
        <button
          type="button"
          onClick={onOpenPalette}
          aria-label="搜索并关注更多代码"
          className="flex h-14 items-center justify-center rounded-md border border-dashed border-line-strong text-ink-400 transition-colors duration-fast hover:border-brand-400 hover:text-brand-600"
        >
          <Icon name="plus" size={16} />
        </button>
      </div>
      {!loading && top.length === 0 && <p className="mt-3 text-micro text-ink-400">IV 数据暂缺 · 留空而非编造</p>}
    </div>
  );
}

/* ---------- 2. 波动率洞察卡 ---------- */
function insightText(avgRank: number): { level: string; hint: React.ReactNode } {
  if (avgRank < 35) {
    return {
      level: '一年低位',
      hint: (
        <>
          <strong className="font-semibold text-up-700">权利金相对便宜</strong>
          ，买入保护性看跌或布局看涨价差的成本较低，适合逢低建立对冲仓位。
        </>
      ),
    };
  }
  if (avgRank > 65) {
    return {
      level: '一年高位',
      hint: (
        <>
          权利金定价偏贵，<strong className="font-semibold text-up-700">备兑卖出</strong>
          与价差卖方策略性价比更高，但需留意波动回落前的尾部风险。
        </>
      ),
    };
  }
  return {
    level: '中枢附近',
    hint: (
      <>
        期权定价接近一年中枢，<strong className="font-semibold text-up-700">方向性策略</strong>
        优于波动率策略，等待 IV 显著偏离再出手。
      </>
    ),
  };
}

function IvInsightCard({ sectorName, rows }: { sectorName: string; rows: IvRowVm[] }) {
  const ranks = rows.map((r) => r.rank).filter((v): v is number => v !== null);
  const avg = ranks.length ? Math.round(ranks.reduce((a, b) => a + b, 0) / ranks.length) : null;
  const insight = avg !== null ? insightText(avg) : null;
  return (
    <div className="rounded-lg border border-brand-100 bg-brand-50 p-5">
      <div className="flex items-center justify-between">
        <p className="eyebrow !text-brand-700">波动率洞察</p>
        <Icon name="spark-ai" size={16} className="text-ai-600" />
      </div>
      {avg === null || !insight ? (
        <p className="mt-3 text-body-s text-ink-500">IV 样本不足，暂无法生成洞察 · 留空而非编造</p>
      ) : (
        <p className="mt-3 font-display text-[15px] leading-[24px] text-ink-800">
          当前{sectorName}板块综合 IV 排名 <span className="font-mono tnum">{avg}</span>%，处于
          <strong className="font-semibold text-up-700">{insight.level}</strong>。{insight.hint}
        </p>
      )}
      <p className="mt-3 text-micro text-ink-400">基于板块成分 IV 百分位均值 · 非投资建议</p>
    </div>
  );
}

/* ---------- 3. 板块相关性卡 ---------- */
function CorrRow({ label, value }: { label: string; value: number | null }) {
  const v = value !== null ? Math.max(-1, Math.min(1, value)) : null;
  return (
    <div className="flex items-center justify-between gap-3 border-t border-line py-2.5 first:border-0">
      <span className="text-caption text-ink-500">{label}</span>
      <span className="flex items-center gap-3">
        <span className="relative h-1 w-24 rounded-pill bg-line" role="presentation">
          <span className="absolute left-1/2 top-1/2 h-2 w-px -translate-x-1/2 -translate-y-1/2 bg-line-strong" aria-hidden="true" />
          {v !== null && (
            <span
              className={cn('absolute top-0 h-full origin-left animate-grow-bar rounded-pill', v >= 0 ? 'bg-brand-600' : 'bg-ink-300')}
              style={v >= 0 ? { left: '50%', width: `${v * 50}%` } : { left: `${50 + v * 50}%`, width: `${-v * 50}%` }}
            />
          )}
        </span>
        <span className="w-14 text-right font-mono text-data-m text-ink-800 tnum">
          {v !== null ? `${v >= 0 ? '+' : '−'}${Math.abs(v).toFixed(2)}` : '—'}
        </span>
      </span>
    </div>
  );
}

function CorrCard({ sector }: { sector: SectorVm | null }) {
  return (
    <div className="card-surface p-5">
      <div className="flex items-center justify-between">
        <p className="eyebrow">板块相关性 · 90 日</p>
        <Icon name="crosshair" size={16} className="text-ink-400" />
      </div>
      <div className="mt-3">
        <CorrRow label="对比标普 500" value={sector?.corr?.spy ?? null} />
        <CorrRow label="对比纳指 100" value={sector?.corr?.ndx ?? null} />
        <CorrRow label="对比美债 20Y+" value={sector?.corr?.ust20y ?? null} />
      </div>
    </div>
  );
}

/* ---------- 侧栏组装 ---------- */
interface SideRailProps {
  sector: SectorVm | null;
  rows: IvRowVm[];
  loading: boolean;   // sectors 首载
  ivLoading: boolean; // iv-ranking 加载（含切换板块）
  onOpenTicker: (t: string) => void;
  onOpenPalette: () => void;
}

export default function SideRail({ sector, rows, loading, ivLoading, onOpenTicker, onOpenPalette }: SideRailProps) {
  if (loading) {
    return (
      <div className="grid grid-cols-1 gap-4 self-start md:grid-cols-2 lg:grid-cols-1" aria-hidden="true">
        <SkeletonCard />
        <SkeletonCard />
        <SkeletonCard />
      </div>
    );
  }
  return (
    <div className="grid grid-cols-1 gap-4 self-start md:grid-cols-2 lg:grid-cols-1">
      <IvHeatCard rows={rows} loading={ivLoading} onOpenTicker={onOpenTicker} onOpenPalette={onOpenPalette} />
      <IvInsightCard sectorName={sector?.name ?? '—'} rows={rows} />
      <CorrCard sector={sector} />
    </div>
  );
}
