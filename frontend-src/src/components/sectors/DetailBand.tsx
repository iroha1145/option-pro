/**
 * B2 板块详情带（sectors.md：选中时出现，accordion 320ms ease-paper，内部双栏）
 * - 左栏（7 列）：成分股 mini 表（代码/价/涨跌/强度，前 8 只，行高 40px）
 *   表头：板块名 Serif 18 +「查看全部 N 只」→ /screener?sector=id；行点击 → 详情抽屉
 * - 右栏（5 列）：板块强度趋势 30 日（§6-2 点阵面积，draw-line 1000ms）
 *   + 三项对照 Mono 行（对比 SPY 相对强度 / 5 日资金流评级 / 板块 IV 均值）
 * - 空板块：「该板块暂无成分数据」+ empty-chart.svg
 */
import { useMemo } from 'react';
import { Link } from 'react-router';
import { motion } from 'framer-motion';
import { cn } from '@/lib/utils';
import { fmtPct, fmtPrice } from '@/lib/format';
import ReactECharts from '@/components/charts/ReactECharts';
import TickerLogo from '@/components/shared/TickerLogo';
import ChangeBadge from '@/components/shared/ChangeBadge';
import StrengthBar from '@/components/shared/StrengthBar';
import Icon from '@/components/icons';
import { CH, baseAnimation, glassTooltip, stippleAreaStyle, type ChartOption } from '@/lib/chart';
import type { SectorVm } from './model';

/* ---------- 三项对照行 ---------- */
function CompareRow({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between border-t border-line py-2.5 first:border-0">
      <span className="text-caption text-ink-500">{label}</span>
      <span className="font-mono text-data-m text-ink-800 tnum">{children}</span>
    </div>
  );
}

function FlowDots({ rating }: { rating: number }) {
  const n = Math.max(0, Math.min(5, Math.round(rating)));
  return (
    <span className="inline-flex items-center gap-1" aria-label={`资金流评级 ${n}/5`}>
      {Array.from({ length: 5 }, (_, i) => (
        <span key={i} className={cn('text-[9px] leading-none', i < n ? 'text-brand-600' : 'text-ink-300')} aria-hidden="true">
          ●
        </span>
      ))}
      <span className="ml-1 font-mono text-micro text-ink-400 tnum">{n}/5</span>
    </span>
  );
}

/* ---------- 30 日强度趋势（点阵面积 §6-2） ---------- */
function TrendChart({ sector }: { sector: SectorVm }) {
  const option = useMemo<ChartOption>(() => {
    const data = sector.trend30d;
    /* 以数据自带 asOf 为锚（保持渲染纯净）；缺失时退化为序号 */
    const anchor = sector.asOf ? new Date(sector.asOf).getTime() : null;
    const labels = data.map((_, i) => {
      if (anchor === null || Number.isNaN(anchor)) return `${i + 1}`;
      const d = new Date(anchor - (data.length - 1 - i) * 86_400_000);
      return `${d.getMonth() + 1}/${d.getDate()}`;
    });
    return {
      ...baseAnimation,
      animationDuration: 1000,
      grid: { left: 2, right: 6, top: 10, bottom: 2, containLabel: false },
      tooltip: glassTooltip({
        formatter: (ps: unknown) => {
          const arr = ps as { axisValue?: string; data?: number }[];
          const p = arr?.[0];
          if (!p) return '';
          return `<span style="font-family:'IBM Plex Mono',monospace;font-size:12px">${p.axisValue} · 强度 <b>${Number(p.data).toFixed(1)}</b></span>`;
        },
      }),
      xAxis: { type: 'category', data: labels, show: false, boundaryGap: false },
      yAxis: {
        type: 'value',
        show: false,
        splitLine: { show: false },
        min: (v: { min: number }) => Math.floor(v.min - 3),
        max: (v: { max: number }) => Math.ceil(v.max + 3),
      },
      series: [
        {
          type: 'line',
          data,
          symbol: 'none',
          smooth: 0.3,
          lineStyle: { color: CH.brand500, width: 2 },
          areaStyle: stippleAreaStyle(),
        },
      ],
    };
  }, [sector]);

  if (sector.trend30d.length < 2) {
    return (
      <div className="flex h-36 flex-col items-center justify-center rounded-md border border-dashed border-line-strong bg-card-warm text-center">
        <Icon name="candle" size={20} className="text-ink-300" />
        <p className="mt-2 text-micro text-ink-400">趋势快照缺失 · 留空而非编造</p>
      </div>
    );
  }
  return (
    <div className="h-36 w-full" role="img" aria-label={`${sector.name} 30 日强度趋势`}>
      <ReactECharts option={option} ariaLabel={`${sector.name} 30 日强度趋势`} />
    </div>
  );
}

/* ---------- 详情带 ---------- */
interface DetailBandProps {
  sector: SectorVm;
  onOpenTicker: (ticker: string) => void;
}

export default function DetailBand({ sector, onOpenTicker }: DetailBandProps) {
  const top = sector.constituents.slice(0, 8);
  return (
    <motion.section
      key={sector.id}
      initial={{ height: 0, opacity: 0 }}
      animate={{ height: 'auto', opacity: 1 }}
      exit={{ height: 0, opacity: 0 }}
      transition={{ duration: 0.32, ease: [0.16, 1, 0.3, 1] }}
      className="overflow-hidden"
      aria-label={`${sector.name} 板块详情`}
    >
      <div className="card-surface mt-6 p-4 md:p-6">
        <div className="grid grid-cols-1 gap-6 lg:grid-cols-12">
          {/* 左栏：成分股 mini 表 */}
          <div className="lg:col-span-7">
            <div className="flex flex-wrap items-baseline justify-between gap-2 border-b border-line pb-3">
              <h2 className="font-display text-[18px] leading-[24px] font-semibold text-ink-900">{sector.name} · 成分概览</h2>
              <Link
                to={`/screener?sector=${encodeURIComponent(sector.id)}`}
                className="flex items-center gap-1 text-caption text-brand-600 transition-colors hover:text-brand-500"
              >
                查看全部 {sector.count} 只
                <Icon name="arrow-up-right" size={12} />
              </Link>
            </div>

            {top.length === 0 ? (
              <div className="flex flex-col items-center py-8 text-center">
                <img src="/empty-chart.svg" alt="" width={150} height={94} className="h-auto w-[150px] opacity-95" loading="lazy" />
                <p className="mt-3 text-body-s text-ink-500">该板块暂无成分数据</p>
              </div>
            ) : (
              <ul className="divide-y divide-line">
                {top.map((c) => (
                  <li key={c.ticker}>
                    <button
                      type="button"
                      onClick={() => onOpenTicker(c.ticker)}
                      className="group flex h-10 w-full items-center gap-3 px-1 text-left transition-colors duration-fast hover:bg-paper-2"
                      aria-label={`打开 ${c.ticker} 详情`}
                    >
                      <TickerLogo ticker={c.ticker} size={24} />
                      <span className="w-16 font-mono text-body-s font-semibold text-ink-800">{c.ticker}</span>
                      <span className="hidden min-w-0 flex-1 truncate text-micro text-ink-400 sm:inline">{c.name}</span>
                      <span className="ml-auto w-20 text-right font-mono text-body-s text-ink-800 tnum">
                        {c.price !== null ? fmtPrice(c.price) : '—'}
                      </span>
                      <span className="w-24 text-right">
                        {c.changePct !== null ? <ChangeBadge value={c.changePct} size="sm" /> : <span className="font-mono text-ink-300">—</span>}
                      </span>
                      <span className="hidden w-28 justify-end md:flex">
                        {c.strengthScore !== null ? (
                          <StrengthBar score={c.strengthScore} width={56} />
                        ) : (
                          <span className="font-mono text-ink-300">—</span>
                        )}
                      </span>
                      <span className="inline-flex size-6 items-center justify-center rounded-xs border border-line bg-card text-ink-400 opacity-0 transition-opacity duration-fast group-hover:opacity-100">
                        <Icon name="arrow-up-right" size={12} />
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>

          {/* 右栏：30 日强度趋势 + 三项对照 */}
          <div className="lg:col-span-5">
            <p className="eyebrow">板块强度趋势 · 30 日</p>
            <div className="mt-3">
              <TrendChart sector={sector} />
            </div>
            <div className="mt-4">
              <CompareRow label="对比 SPY 相对强度">
                {sector.rsVsSpy !== null ? (
                  <span className={cn(sector.rsVsSpy >= 0 ? 'text-up-700' : 'text-down-700')}>{fmtPct(sector.rsVsSpy)}</span>
                ) : (
                  '—'
                )}
              </CompareRow>
              <CompareRow label="5 日资金流评级">
                {sector.flowRating !== null ? <FlowDots rating={sector.flowRating} /> : '—'}
              </CompareRow>
              <CompareRow label="板块 IV 均值">
                {sector.ivAvg !== null ? `${sector.ivAvg.toFixed(1)}%` : '—'}
              </CompareRow>
            </div>
          </div>
        </div>
      </div>
    </motion.section>
  );
}
