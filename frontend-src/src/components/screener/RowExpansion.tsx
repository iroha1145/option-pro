/**
 * 行展开分项明细（screener.md B2 行展开 · accordion 260ms）
 * ① 分项强度 breakdown（与行内微条同源 subscoreDimsOf；4 条 grow-bar + 分值 + 权重）
 * ② 迷你点阵面积图（§6-2 stipple）：mock 用行内 sparkline；live 契约无 sparkline →
 *    按需拉真实日 K（stocksApi.chart range=1d）取近 6 根收盘；拿不到如实空态，杜绝 Infinity
 * ③ 操作（打开详情 / 相关突破事件）+ 信号 + 成交额
 */
import { useEffect, useState } from 'react';
import { Link } from 'react-router';
import { motion } from 'framer-motion';
import { stocksApi } from '@/api/modules/stocks';
import { ApiError } from '@/api/client';
import type { ScreenerRow, Signal } from '@/api/types';
import { cn } from '@/lib/utils';
import { fmtCompact } from '@/lib/format';
import Icon from '@/components/icons';
import SignalChip from '@/components/shared/SignalChip';
import Sparkline from '@/components/charts/Sparkline';
import { SkeletonBlock } from '@/components/shared/Skeleton';
import { strengthBarClass } from '@/components/shared/StrengthBar';
import InfoHint from '@/components/shared/InfoHint';
import { SCORE_HINTS, type ScoreHint } from '@/lib/scoreHints';
import { subscoreDimsOf } from './types';
import ManualStockPull from '@/components/detail/ManualStockPull';

const EASE_PAPER = [0.16, 1, 0.3, 1] as [number, number, number, number];

/* live 契约分项键 → 评分解释（mock 四维 trend/momentum/… 无对应文案则不渲染图标） */
const DIM_HINTS: Record<string, ScoreHint> = {
  score_short: SCORE_HINTS.strengthShort,
  score_mid: SCORE_HINTS.strengthMid,
  score_long: SCORE_HINTS.strengthLong,
  breakout_quality_score: SCORE_HINTS.strengthBreakoutQuality,
};

/* ---------------- 近 6 日收盘（live 懒加载；表格/卡片双实例只共享进行中的请求） ---------------- */
const DOT_DAYS = 6;
const closesCache = new Map<string, Promise<number[] | null>>();

function fetchDailyCloses(ticker: string, force = false): Promise<number[] | null> {
  if (force) closesCache.delete(ticker);
  let p = closesCache.get(ticker);
  if (!p) {
    p = stocksApi
      .chart(ticker, '1d', 'raw', force) // 后端真实日 K 周期
      .then((c) => {
        const closes = c.candles
          .map((b) => b.c)
          .filter((v) => Number.isFinite(v) && v > 0)
          .slice(-DOT_DAYS);
        return closes.length >= 2 ? closes : null;
      })
      .catch((error) => {
        throw error;
      })
      .finally(() => {
        // 成功数据的新鲜度由 marketGet 统一管理。这里只合并并发请求，
        // 不能在单页会话中永久冻结第一次展开时的六日收盘。
        if (closesCache.get(ticker) === p) closesCache.delete(ticker);
      });
    closesCache.set(ticker, p);
  }
  return p;
}

/**
 * 点阵面积块三态：undefined 加载中 · null 数据不可用（诚实空态） · number[] 真实收盘
 * mock 行自带 sparkline 直接使用；live 行 sparkline 恒空 → 拉真实日 K。
 */
function DotMatrixBlock({ row }: { row: ScreenerRow }) {
  const hasSpark = row.sparkline.length >= 2;
  const [closes, setCloses] = useState<number[] | null | undefined>(hasSpark ? row.sparkline : undefined);
  const [loadError, setLoadError] = useState<string | null>(null);

  useEffect(() => {
    if (hasSpark) {
      setCloses(row.sparkline);
      setLoadError(null);
      return;
    }
    let alive = true;
    setCloses(undefined);
    setLoadError(null);
    void fetchDailyCloses(row.ticker)
      .then((v) => {
        if (alive) setCloses(v);
      })
      .catch((error: unknown) => {
        if (!alive) return;
        setCloses(null);
        setLoadError(error instanceof ApiError ? error.message : '暂时取不到日线数据');
      });
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [row.ticker, hasSpark]);

  const title = hasSpark
    ? '近 5 日 · 点阵面积'
    : Array.isArray(closes)
      ? `近 ${closes.length} 日 · 点阵面积`
      : '日线 · 点阵面积';

  const refreshAfterPull = () => {
    setCloses(undefined);
    setLoadError(null);
    void fetchDailyCloses(row.ticker, true)
      .then(setCloses)
      .catch((error: unknown) => {
        setCloses(null);
        setLoadError(error instanceof ApiError ? error.message : '暂时取不到日线数据');
      });
  };

  return (
    <div>
      <p className="eyebrow">{title}</p>
      <div className="mt-3 rounded-md border border-line bg-card p-3">
        {closes === undefined ? (
          <SkeletonBlock className="h-[72px] w-full rounded-sm" />
        ) : closes === null ? (
          /* 接口拿不到日线：如实留空，严禁 Infinity/编造 */
          <div className="flex min-h-[96px] flex-col items-center justify-center gap-1.5 text-center">
            <Icon name="candle" size={16} className="text-ink-300" />
            <p className="text-caption text-ink-400">{loadError ?? '日线数据暂不可用'}</p>
            <ManualStockPull ticker={row.ticker} onPulled={refreshAfterPull} compact />
          </div>
        ) : (
          <>
            <Sparkline
              data={closes}
              width={260}
              height={72}
              change={closes[closes.length - 1] - closes[0]}
              variant="area"
              className="w-full"
            />
            <div className="mt-2 flex items-center justify-between font-mono text-micro text-ink-400 tnum">
              <span>低 {Math.min(...closes).toFixed(1)}</span>
              <span>高 {Math.max(...closes).toFixed(1)}</span>
            </div>
          </>
        )}
      </div>
    </div>
  );
}

export interface RowExpansionProps {
  row: ScreenerRow;
  weights: { trend: number; momentum: number; volume: number; volatility: number } | null;
  dollarVolume: number | null;
  signals: Signal[] | null;
  onOpenDetail: (ticker: string) => void;
}

export default function RowExpansion({ row, weights, dollarVolume, signals, onOpenDetail }: RowExpansionProps) {
  const dims = subscoreDimsOf(row);
  // 权重仅对 mock 四维键有意义（live 契约 profiles 无权重 → weights 为 null 自动隐藏）
  const weightOf = (key: string): number | null =>
    weights && key in weights ? weights[key as keyof typeof weights] : null;
  return (
    <div className="grid grid-cols-1 gap-x-8 gap-y-5 border-t border-line bg-card-warm/60 px-4 py-4 md:grid-cols-3">
      {/* ① 分项强度 breakdown（与行内微条同源） */}
      <div>
        <p className="eyebrow">分项强度 · BREAKDOWN</p>
        <div className="mt-3 space-y-2.5">
          {dims.map(({ key, label, value }, i) => {
            const w = weightOf(key);
            return (
              <div key={key} className="grid grid-cols-[56px_1fr_64px] items-center gap-2.5">
                <span className="whitespace-nowrap text-caption text-ink-500">
                  {label}
                  {DIM_HINTS[key] && <InfoHint hint={DIM_HINTS[key]} side="bottom" size={11} className="ml-0.5" />}
                </span>
                <span className="h-1.5 overflow-hidden rounded-pill bg-line" role="presentation">
                  {value !== null && (
                    <motion.span
                      className={cn('block h-full origin-left rounded-pill', strengthBarClass(value))}
                      initial={{ scaleX: 0 }}
                      animate={{ scaleX: 1 }}
                      transition={{ duration: 0.7, ease: EASE_PAPER, delay: i * 0.05 }}
                      style={{ width: `${Math.max(2, Math.min(100, value))}%` }}
                    />
                  )}
                </span>
                <span className="text-right font-mono text-caption text-ink-800 tnum">
                  {value !== null ? value : '—'}
                  {w !== null && <span className="ml-1 text-micro text-ink-300">×{w}%</span>}
                </span>
              </div>
            );
          })}
        </div>
        {weights && <p className="mt-2.5 text-micro text-ink-400">权重来自当前评分方法（右侧栏）</p>}
      </div>

      {/* ② 迷你点阵面积图（真实日 K / mock sparkline；空态诚实） */}
      <DotMatrixBlock row={row} />

      {/* ③ 操作 + 信号 + 成交额 */}
      <div>
        <p className="eyebrow">操作与信号</p>
        <div className="mt-3 flex flex-col items-start gap-2">
          <button
            onClick={() => onOpenDetail(row.ticker)}
            className="flex items-center gap-1.5 rounded-md border border-line bg-card px-3 py-1.5 text-caption text-ink-600 transition-colors duration-fast hover:border-brand-400 hover:text-brand-600"
          >
            <Icon name="arrow-up-right" size={13} />
            打开详情
          </button>
          <Link
            to="/breakouts"
            className="flex items-center gap-1.5 rounded-md border border-line bg-card px-3 py-1.5 text-caption text-ink-600 transition-colors duration-fast hover:border-brand-400 hover:text-brand-600"
          >
            <Icon name="radar" size={13} />
            相关突破事件
          </Link>
        </div>
        <div className="mt-3.5 border-t border-line pt-3">
          <p className="mb-1.5 text-micro text-ink-400">活跃信号</p>
          {signals === null ? (
            <div className="flex gap-1.5" aria-hidden="true">
              <span className="skeleton-shimmer h-5 w-14 rounded-xs" />
              <span className="skeleton-shimmer h-5 w-14 rounded-xs" />
            </div>
          ) : signals.length === 0 ? (
            <p className="text-caption text-ink-400">— 暂无信号</p>
          ) : (
            <span className="flex flex-wrap gap-1.5">
              {signals.map((s, i) => (
                <SignalChip key={i} type={s.type} label={s.label} />
              ))}
            </span>
          )}
        </div>
        <div className="mt-3 flex items-center justify-between border-t border-line pt-3">
          <span className="text-micro text-ink-400">美元成交额（推导）</span>
          <span className="font-mono text-data-m text-ink-800 tnum">
            {dollarVolume === null ? '—' : fmtCompact(dollarVolume)}
          </span>
        </div>
      </div>
    </div>
  );
}
