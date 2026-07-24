/**
 * LeadBigCard · 当日信号 lead 事件「压缩版大事件卡」（Paper Terminal 皮肤，7/12 宽）
 * 结构：状态 chips 行（lg 合并 meta 行）+ 「首要信号」徽章 → Serif Display-M 标题（ticker 开抽屉）
 *      → K线迷你图（ECharts 蜡烛 150px）→ 三价位行 + 优先级环（右上同排）
 *      → 生命周期步进条 → 4 评分条 + 贡献分段条 → 风险提醒 → 底部版本与按钮
 * 数据：breakouts/current 事件 + breakouts/events/{id} 详情补全（宽松字段合并，缺字段显「—」）
 * 动效：rise-in 进场 · count-up 优先级环 · grow-bar 错峰 · draw-line 环弧 · 现价 tick-flash
 */
import { useEffect, useMemo, useRef, useState } from 'react';
import { Link } from 'react-router';
import { motion } from 'framer-motion';
import { ApiError } from '@/api/client';
import { breakoutsApi } from '@/api/modules/breakouts';
import { stocksApi } from '@/api/modules/stocks';
import type { Candle } from '@/api/types';
import { usePolling } from '@/hooks/usePolling';
import { useCountUp } from '@/hooks/useCountUp';
import { useShell } from '@/components/Layout';
import ReactECharts from '@/components/charts/ReactECharts';
import ChangeBadge from '@/components/shared/ChangeBadge';
import InfoHint from '@/components/shared/InfoHint';
import { SCORE_HINTS } from '@/lib/scoreHints';
import { SkeletonBlock } from '@/components/shared/Skeleton';
import Icon from '@/components/icons';
import { cn } from '@/lib/utils';
import { fmtPrice, fmtRelative } from '@/lib/format';
import { baseAnimation, CH, glassTooltip, type ChartOption } from '@/lib/chart';
import {
  asFullDetail,
  LIFECYCLE_CHIP_CLASS,
  LIFECYCLE_CN,
  LIFECYCLE_TONE,
  SESSION_CN,
  SETUP_CN,
  riskBarClass,
  scoreBarClass,
} from './types';
import type { BreakoutCurrentEvent, BreakoutEventFull, BreakoutSession, LifecycleState } from './types';

const EASE_PAPER = [0.16, 1, 0.3, 1] as [number, number, number, number];
const MONO = '"IBM Plex Mono", monospace';

/* ---------------- 工具 ---------------- */
const pad2 = (n: number) => String(n).padStart(2, '0');

/** 事件时间 M/DD HH:mm（美东快照时间，与全站本地时区口径一致） */
function fmtEventTime(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '—';
  return `${d.getMonth() + 1}/${pad2(d.getDate())} ${pad2(d.getHours())}:${pad2(d.getMinutes())}`;
}

/** 观测 x 小时前（不足 1 小时显分钟） */
function observedAgo(iso: string): string {
  const ms = Date.now() - new Date(iso).getTime();
  if (!Number.isFinite(ms) || ms < 0) return '—';
  const h = ms / 3_600_000;
  if (h >= 1) return `${h >= 10 ? Math.round(h) : h.toFixed(1)} 小时前`;
  return `${Math.max(1, Math.round(ms / 60_000))} 分钟前`;
}

const num = (v: unknown): number | null => (typeof v === 'number' && Number.isFinite(v) ? v : null);
const str = (v: unknown): string | null => (typeof v === 'string' && v ? v : null);

/* ---------------- 时段 chip（§1.6 LED 色） ---------------- */
const SESSION_DOT: Record<BreakoutSession, string> = {
  premarket: 'bg-warn-600',
  regular: 'bg-up-600',
  postmarket: 'bg-ai-600',
  closed: 'bg-ink-400',
};

/* ---------------- 告警优先级环（64px 紧凑版：轨道 line + brand-600 弧 draw-line + count-up） ---------------- */
function PriorityRing({ score }: { score: number }) {
  const v = useCountUp(score, 900);
  const r = 24;
  const c = 2 * Math.PI * r;
  const frac = Math.max(0, Math.min(100, score)) / 100;
  return (
    <div className="flex shrink-0 flex-col items-center" aria-label={`告警优先级 ${score}`}>
      <div className="relative size-[64px]">
        <svg viewBox="0 0 64 64" className="size-full -rotate-90" aria-hidden="true">
          <circle cx="32" cy="32" r={r} fill="none" stroke="var(--line)" strokeWidth="5" />
          <motion.circle
            cx="32"
            cy="32"
            r={r}
            fill="none"
            stroke="var(--brand-600)"
            strokeWidth="5"
            strokeLinecap="round"
            strokeDasharray={c}
            initial={{ strokeDashoffset: c }}
            animate={{ strokeDashoffset: c * (1 - frac) }}
            transition={{ duration: 1.1, ease: EASE_PAPER }}
          />
        </svg>
        <div className="absolute inset-0 flex items-center justify-center">
          <span className="font-mono text-[15px] font-medium leading-[20px] text-ink-900 tnum">{Math.round(v)}</span>
        </div>
      </div>
      <span className="mt-0.5 whitespace-nowrap text-[10px] leading-[13px] text-ink-400">
        告警优先级
        <InfoHint hint={SCORE_HINTS.breakoutPriority} side="top" align="end" size={11} className="ml-0.5" />
      </span>
    </div>
  );
}

/* ---------------- 生命周期步进条（五节点 + FAILED/EXPIRED 末端标记） ---------------- */
const STEPS = [
  { key: 'DISCOVERED', label: '已发现' },
  { key: 'TRIGGERED', label: '已触发' },
  { key: 'CONFIRMED', label: '已确认' },
  { key: 'HOLDING', label: '保持中' },
  { key: 'RETESTING', label: '回踩中' },
] as const;

function stepIndex(state: LifecycleState): number {
  switch (state) {
    case 'DISCOVERED':
    case 'WATCHING':
      return 0;
    case 'TRIGGERED':
      return 1;
    case 'CONFIRMED':
      return 2;
    case 'HOLDING':
      return 3;
    default:
      return 4; // RETESTING / RETEST_HELD / REACCELERATING / EXTENDED
  }
}

function LifecycleStepper({ state }: { state: LifecycleState }) {
  const terminal = state === 'FAILED' || state === 'EXPIRED' ? state : null;
  /* FAILED/EXPIRED：轨迹必经 已发现→已触发（mock transitions 同构），末端红/灰标记当前态 */
  const idx = terminal ? -1 : stepIndex(state);
  const pastTo = terminal ? 1 : idx - 1;

  const node = (label: string, tone: 'past' | 'current' | 'future' | 'down' | 'ink-end', key: string) => (
    <div key={key} className="flex min-w-[44px] flex-col items-center">
      <span
        className={cn(
          'size-2.5 rounded-full',
          tone === 'current' && 'bg-brand-600 ring-2 ring-brand-600/25 animate-led-pulse',
          tone === 'past' && 'bg-brand-600',
          tone === 'future' && 'border border-ink-300 bg-card',
          tone === 'down' && 'bg-down-600 ring-2 ring-down-600/25',
          tone === 'ink-end' && 'bg-ink-400 ring-2 ring-ink-400/25',
        )}
        aria-hidden="true"
      />
      <span
        className={cn(
          'mt-1.5 whitespace-nowrap text-[10px] leading-[14px]',
          tone === 'current' && 'font-semibold text-brand-600',
          tone === 'past' && 'text-ink-400',
          tone === 'future' && 'text-ink-300',
          tone === 'down' && 'font-semibold text-down-700',
          tone === 'ink-end' && 'font-semibold text-ink-500',
        )}
      >
        {label}
      </span>
    </div>
  );

  const items: { el: ReturnType<typeof node>; passed: boolean }[] = STEPS.map((s, i) => ({
    el: node(s.label, i === idx ? 'current' : i <= pastTo ? 'past' : 'future', s.key),
    passed: terminal ? i < STEPS.length - 1 : i < idx,
  }));
  if (terminal) {
    items.push({
      el: node(LIFECYCLE_CN[terminal], terminal === 'FAILED' ? 'down' : 'ink-end', terminal),
      passed: false,
    });
  }

  return (
    <ol className="no-scrollbar flex items-start overflow-x-auto" aria-label={`生命周期：${LIFECYCLE_CN[state] ?? state}`}>
      {items.map((it, i) => (
        <li key={i} className="flex flex-1 items-start last:flex-none">
          {it.el}
          {i < items.length - 1 && (
            <span className={cn('mx-1 mt-[5px] h-px min-w-3 flex-1', it.passed ? 'bg-brand-400/60' : 'bg-line-strong')} aria-hidden="true" />
          )}
        </li>
      ))}
    </ol>
  );
}

/* ---------------- K线迷你图（蜡烛 · 发丝网格 · 十字光标 · 诚实空态） ---------------- */
function buildMiniOption(bars: Candle[]): ChartOption {
  const labels = bars.map((b) => {
    const d = new Date(b.t);
    return `${pad2(d.getHours())}:${pad2(d.getMinutes())}`;
  });
  return {
    ...baseAnimation,
    grid: { left: 6, right: 6, top: 10, bottom: 4, containLabel: true },
    xAxis: {
      type: 'category' as const,
      data: labels,
      axisLine: { show: false },
      axisTick: { show: false },
      axisLabel: { color: CH.ink400, fontSize: 10, fontFamily: MONO, hideOverlap: true },
    },
    yAxis: {
      type: 'value' as const,
      scale: true,
      position: 'right' as const,
      axisLine: { show: false },
      axisTick: { show: false },
      axisLabel: { color: CH.ink400, fontSize: 10, fontFamily: MONO },
      splitLine: { lineStyle: { color: CH.lineChart, width: 1 } },
    },
    tooltip: glassTooltip({
      trigger: 'axis',
      axisPointer: {
        type: 'cross' as const,
        lineStyle: { color: CH.ink300, width: 1, type: [3, 3] as number[] },
        crossStyle: { color: CH.ink300, width: 1, type: [3, 3] as number[] },
        label: {
          backgroundColor: 'rgba(253,252,249,.92)',
          borderColor: '#E9E7E0',
          borderWidth: 1,
          color: '#5A6788',
          fontFamily: MONO,
          fontSize: 10,
        },
      },
      formatter: (params: unknown) => {
        const arr = params as { dataIndex: number }[];
        const b = bars[arr[0]?.dataIndex ?? 0];
        if (!b) return '';
        const chg = b.c - b.o;
        const color = chg >= 0 ? CH.up600 : CH.down600;
        const d = new Date(b.t);
        return (
          `<div style="font-family:${MONO};font-size:12px;line-height:19px">` +
          `<div style="color:#8A94B0">${pad2(d.getHours())}:${pad2(d.getMinutes())}</div>` +
          `开 ${b.o.toFixed(2)} · 高 ${b.h.toFixed(2)}<br/>低 ${b.l.toFixed(2)} · ` +
          `收 <b style="color:${color}">${b.c.toFixed(2)}</b></div>`
        );
      },
    }),
    series: [
      {
        type: 'candlestick' as const,
        data: bars.map((b) => [b.o, b.c, b.l, b.h]),
        itemStyle: {
          color: CH.up600,
          color0: CH.down600,
          borderColor: CH.up600,
          borderColor0: CH.down600,
          borderWidth: 1.2,
        },
        barMaxWidth: 10,
        z: 3,
      },
    ],
  } as ChartOption;
}

function MiniKline({ ticker }: { ticker: string }) {
  const { data, error, loading, refresh } = usePolling(() => stocksApi.chart(ticker, '1d'), null, [ticker]);
  const option = useMemo(() => (data && data.candles.length > 1 ? buildMiniOption(data.candles) : null), [data]);
  /* 突破标的常在焦点池外：503 快照未生成时可手动拉取（与详情页 ManualStockPull 同一预算通道） */
  const [pulling, setPulling] = useState(false);
  const [pullError, setPullError] = useState<string | null>(null);
  useEffect(() => {
    setPullError(null);
    setPulling(false);
  }, [ticker]);
  const snapshotMissing =
    error instanceof ApiError && (error.bizCode === 'public_snapshot_unavailable' || error.code === 503);

  const pullAndReload = async () => {
    if (pulling) return;
    setPulling(true);
    setPullError(null);
    try {
      await stocksApi.pull(ticker);
      refresh();
    } catch (cause) {
      setPullError(
        cause instanceof ApiError
          ? `${cause.message}${cause.retryAfter ? ` · ${cause.retryAfter} 秒后可重试` : ''}`
          : '拉取失败，请稍后重试',
      );
    } finally {
      setPulling(false);
    }
  };

  return (
    <div className="relative h-[120px] overflow-hidden rounded-md border border-line-chart bg-card-warm sm:h-[150px]">
      {loading ? (
        <div className="absolute inset-0 p-3" aria-hidden="true">
          <SkeletonBlock className="h-full w-full rounded-sm border border-line-chart" />
        </div>
      ) : error || !option ? (
        <div className="absolute inset-0 flex flex-col items-center justify-center gap-1 px-4 text-center">
          <img src="/empty-chart.svg" alt="" className="h-12 w-auto opacity-90" loading="lazy" />
          <p className="text-caption font-medium text-ink-600">
            {snapshotMissing ? '当日K线快照未生成' : 'K线读取失败'}
          </p>
          <p className="text-micro text-ink-400">
            {snapshotMissing ? '焦点池外标的，可手动拉取真实行情' : '日线行情暂不可用'}
          </p>
          {pullError && (
            <p role="alert" className="text-micro text-down-700">
              {pullError}
            </p>
          )}
          <div className="mt-1.5 flex items-center gap-1.5">
            {snapshotMissing && (
              <button
                onClick={() => void pullAndReload()}
                disabled={pulling}
                className="inline-flex items-center gap-1.5 rounded-md bg-brand-600 px-2.5 py-1 text-micro font-medium text-white transition-[filter,opacity] duration-fast hover:brightness-105 disabled:cursor-wait disabled:opacity-70"
              >
                {pulling && (
                  <span
                    className="size-2.5 animate-spin rounded-full border-2 border-white/35 border-t-white"
                    aria-hidden="true"
                  />
                )}
                {pulling ? '正在拉取' : '拉取行情'}
              </button>
            )}
            <button
              onClick={refresh}
              className="rounded-md border border-line bg-card px-2.5 py-1 text-micro font-medium text-ink-600 transition-colors duration-fast hover:border-brand-400 hover:text-brand-600"
            >
              重试
            </button>
          </div>
        </div>
      ) : (
        <ReactECharts option={option} ariaLabel={`${ticker} 当日迷你 K 线图`} />
      )}
    </div>
  );
}

/* ---------------- 四维评分条（grow-bar 错峰 + Mono 值） ---------------- */
const BIG_SCORES = [
  { key: 'base_quality_score', label: '突破质量', hint: SCORE_HINTS.breakoutBase },
  { key: 'breakout_confirmation_score', label: '确认强度', hint: SCORE_HINTS.breakoutConfirmation },
  { key: 'data_confidence_score', label: '数据可信度', hint: SCORE_HINTS.breakoutDataConfidence },
  { key: 'chase_risk_score', label: '追高风险', hint: SCORE_HINTS.breakoutChaseRisk },
] as const;

function BigScoreBars({ ev }: { ev: BreakoutEventFull }) {
  return (
    <div className="space-y-2" aria-label="四维评分">
      {BIG_SCORES.map((d, i) => {
        const raw = num(ev[d.key]);
        const v = raw ?? 0;
        return (
          <div key={d.key} className="grid grid-cols-[64px_1fr_36px] items-center gap-2.5">
            <span className="whitespace-nowrap text-caption text-ink-500">
              {d.label}
              <InfoHint hint={d.hint} size={11} className="ml-0.5" />
            </span>
            <div className="h-1 overflow-hidden rounded-pill bg-line">
              <motion.div
                className={cn('h-full origin-left rounded-pill', d.key === 'chase_risk_score' ? riskBarClass(v) : scoreBarClass(v))}
                initial={{ scaleX: 0 }}
                whileInView={{ scaleX: 1 }}
                viewport={{ once: true, amount: 0.4 }}
                transition={{ duration: 0.7, ease: EASE_PAPER, delay: 0.1 + i * 0.06 }}
                style={{ width: `${Math.max(3, Math.min(100, v))}%` }}
              />
            </div>
            <span className="text-right font-mono text-caption text-ink-600 tnum">{raw !== null ? raw.toFixed(1) : '—'}</span>
          </div>
        );
      })}
    </div>
  );
}

/* ---------------- 贡献分段条（5 段一整条 + 图例；无 contribution_breakdown 时按权重×评分推导） ---------------- */
const CONTRIB_DEFS = [
  { key: 'breakout_quality', label: '突破质量', cls: 'bg-brand-600', scoreKey: 'base_quality_score', weight: 0.3 },
  { key: 'intrinsic_strength', label: '内在强度', cls: 'bg-brand-400', scoreKey: 'intrinsic_strength_score', weight: 0.25 },
  { key: 'sector_fit', label: '板块契合', cls: 'bg-up-600', scoreKey: 'sector_fit_score', weight: 0.15 },
  { key: 'market_fit', label: '市场契合', cls: 'bg-ai-600', scoreKey: 'market_fit_score', weight: 0.15 },
  { key: 'data_confidence', label: '数据置信', cls: 'bg-ink-300', scoreKey: 'data_confidence_score', weight: 0.15 },
] as const;

function ContributionBar({ ev }: { ev: BreakoutEventFull }) {
  const { parts, derived } = useMemo(() => {
    const loose = (ev as unknown as { contribution_breakdown?: Record<string, unknown> }).contribution_breakdown;
    const fromApi = loose && typeof loose === 'object';
    const raws = CONTRIB_DEFS.map((d) => {
      if (fromApi) {
        const v = num(loose[d.key]);
        if (v !== null) return { d, v, pct: 0 };
      }
      return { d, v: Math.round(d.weight * (num(ev[d.scoreKey]) ?? 0) * 10) / 10, pct: 0 };
    });
    const total = raws.reduce((s, p) => s + p.v, 0) || 1;
    return { parts: raws.map((p) => ({ ...p, pct: (p.v / total) * 100 })), derived: !fromApi };
  }, [ev]);

  return (
    <div aria-label="评分贡献分解">
      <div className="flex h-1 overflow-hidden rounded-pill bg-line">
        {parts.map((p, i) => (
          <motion.div
            key={p.d.key}
            className={cn('h-full origin-left', p.d.cls)}
            initial={{ scaleX: 0 }}
            whileInView={{ scaleX: 1 }}
            viewport={{ once: true, amount: 0.4 }}
            transition={{ duration: 0.7, ease: EASE_PAPER, delay: 0.15 + i * 0.06 }}
            style={{ width: `${p.pct}%` }}
          />
        ))}
      </div>
      <ul className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1">
        {parts.map((p) => (
          <li key={p.d.key} className="inline-flex items-center gap-1.5">
            <span className={cn('inline-block size-2 rounded-[2px]', p.d.cls)} aria-hidden="true" />
            <span className="text-[11px] leading-[14px] text-ink-500">{p.d.label}</span>
            <span className="font-mono text-[11px] leading-[14px] text-ink-600 tnum">{p.v.toFixed(1)}</span>
          </li>
        ))}
        {derived && (
          <li className="rounded-xs border border-line-strong bg-card-warm px-1.5 py-px text-[10px] leading-[14px] text-ink-400">
            推导
          </li>
        )}
      </ul>
    </div>
  );
}

/* ================= 大卡主体 ================= */
interface LeadBigCardProps {
  ev: BreakoutCurrentEvent;
  flash: 'up' | 'down' | null;
  locate: boolean;
  onOpen: (ev: BreakoutCurrentEvent) => void;
}

export default function LeadBigCard({ ev, flash, locate, onOpen }: LeadBigCardProps) {
  const { openTicker } = useShell();
  const ref = useRef<HTMLElement>(null);
  useEffect(() => {
    if (locate) ref.current?.scrollIntoView({ behavior: 'smooth', block: 'center' });
  }, [locate]);

  /* 详情补全：契约 /breakouts/events/{id}（scores/结构全字段），宽松合并非空字段 */
  const [detail, setDetail] = useState<{ id: string; ev: BreakoutEventFull } | null>(null);
  useEffect(() => {
    let alive = true;
    breakoutsApi
      .eventDetail(ev.event_id)
      .then((d) => {
        if (alive) setDetail({ id: ev.event_id, ev: asFullDetail(d) });
      })
      .catch(() => undefined);
    return () => {
      alive = false;
    };
  }, [ev.event_id]);
  const detailEv = detail && detail.id === ev.event_id ? detail.ev : null;

  const e = useMemo(() => {
    if (!detailEv) return ev;
    const out: Record<string, unknown> = { ...ev };
    for (const [k, v] of Object.entries(detailEv)) {
      if (v !== undefined && v !== null) out[k] = v;
    }
    /* 现价以 current 轮询为准（tick-flash 联动） */
    out.current_price = ev.current_price;
    out.session_change_pct = ev.session_change_pct;
    return out as unknown as BreakoutCurrentEvent;
  }, [ev, detailEv]);

  /* 宽松扩展字段（契约之外的运行时字段，缺失显「—」/省略） */
  const loose = e as unknown as {
    exchange?: unknown;
    score_version?: unknown;
    market_shape?: unknown;
    versions?: unknown;
    warnings?: unknown;
  };
  const exchange = str(loose.exchange) ?? '—';
  const scoreVersion = str(loose.score_version) ?? '—';
  /* market_shape：契约为对象 {state, rules:{state_label}, ...} → 取中文形态标签；字符串则原样 */
  const shapeRec = (loose.market_shape && typeof loose.market_shape === 'object' ? loose.market_shape : {}) as Record<string, unknown>;
  const shapeRules = (shapeRec.rules && typeof shapeRec.rules === 'object' ? shapeRec.rules : {}) as Record<string, unknown>;
  const shapeTxt = str(loose.market_shape) ?? str(shapeRules.state_label) ?? str(shapeRec.state) ?? str(loose.versions) ?? '—';
  const warnings = Array.isArray(loose.warnings) ? loose.warnings.filter((w): w is string => typeof w === 'string' && !!w) : [];

  const gap = num(e.gap_pct);
  const rvol = num(e.rvol_time_of_day);
  const invalid = num(e.invalidation_price);

  return (
    <motion.article
      ref={ref}
      initial={{ opacity: 0, y: 14 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.56, ease: EASE_PAPER }}
      aria-label={`${e.ticker} ${SETUP_CN[e.setup_type] ?? e.setup_type ?? ''} 首要信号大卡`}
      className={cn('card-surface p-5', locate && 'bk-locate')}
    >
      {/* 顶行：状态 chips + 相对时间（lg 合并 meta 行）· 右侧首要信号徽章 */}
      <div className="flex flex-wrap items-center gap-1.5">
        <span
          className={cn(
            'inline-flex items-center whitespace-nowrap rounded-xs border px-1.5 py-px text-micro font-medium leading-[16px]',
            LIFECYCLE_CHIP_CLASS[LIFECYCLE_TONE[e.lifecycle_state] ?? 'ink'],
          )}
        >
          {LIFECYCLE_CN[e.lifecycle_state] ?? e.lifecycle_state ?? '—'}
        </span>
        <span className="inline-flex items-center whitespace-nowrap rounded-xs border border-brand-400/60 bg-brand-50 px-1.5 py-px text-micro font-medium leading-[16px] text-brand-600">
          {SETUP_CN[e.setup_type] ?? e.setup_type ?? '—'}
        </span>
        <span className="inline-flex items-center gap-1.5 whitespace-nowrap rounded-xs border border-line bg-card px-1.5 py-px text-micro leading-[16px] text-ink-500">
          <span className={cn('size-1.5 rounded-full', SESSION_DOT[e.session], e.session !== 'closed' && 'animate-led-pulse')} aria-hidden="true" />
          {SESSION_CN[e.session]}
        </span>
        <span className="font-mono text-micro text-ink-400 tnum">{fmtRelative(e.triggered_at)}</span>
        {/* lg 以上：meta 并入 chips 行 */}
        <span className="hidden items-center gap-1.5 text-micro text-ink-500 lg:inline-flex">
          <span className="text-ink-300" aria-hidden="true">·</span>
          <span>{exchange}</span>
          <span className="text-ink-300" aria-hidden="true">·</span>
          <span>{e.sector}</span>
          <span className="text-ink-300" aria-hidden="true">·</span>
          <span className="font-mono tnum">跳空 {gap !== null ? `${gap >= 0 ? '+' : ''}${gap.toFixed(2)}%` : '—'}</span>
          <span className="text-ink-300" aria-hidden="true">·</span>
          <span className="font-mono tnum">量能 {rvol !== null ? `${rvol.toFixed(1)}×` : '—'}</span>
          <span className="text-ink-300" aria-hidden="true">·</span>
          <span className="font-mono tnum">{fmtEventTime(e.event_at)} 美东</span>
        </span>
        <span className="ml-auto inline-flex items-center gap-1.5 rounded-pill border border-brand-400/50 bg-brand-50 px-2.5 py-1 text-micro font-medium text-brand-600">
          <Icon name="radar" size={12} />
          首要信号
        </span>
      </div>

      {/* 标题区（ticker 点击开右侧抽屉，保留雷达上下文） */}
      <div className="mt-2.5">
        <h3 className="font-display text-display-m text-ink-900">
          {e.name}{' '}
          <button
            onClick={() => openTicker(e.ticker)}
            aria-label={`打开 ${e.ticker} 个股详情抽屉`}
            className="text-brand-600 underline-offset-4 transition-colors hover:text-brand-500 hover:underline"
          >
            {e.ticker}
          </button>
        </h3>
        <p className="mt-1 flex flex-wrap items-center gap-x-1.5 gap-y-1 text-caption text-ink-500 lg:hidden">
          <span>{exchange}</span>
          <span className="text-ink-300" aria-hidden="true">·</span>
          <span>{e.sector}</span>
          <span className="text-ink-300" aria-hidden="true">·</span>
          <span className="font-mono tnum">跳空 {gap !== null ? `${gap >= 0 ? '+' : ''}${gap.toFixed(2)}%` : '—'}</span>
          <span className="text-ink-300" aria-hidden="true">·</span>
          <span className="font-mono tnum">同时段量能 {rvol !== null ? `${rvol.toFixed(1)}×` : '—'}</span>
          <span className="text-ink-300" aria-hidden="true">·</span>
          <span className="font-mono tnum">事件时间 {fmtEventTime(e.event_at)} 美东</span>
        </p>
      </div>

      {/* K线迷你图 */}
      <div className="mt-3">
        <MiniKline ticker={e.ticker} />
      </div>

      {/* 三价位行 + 告警优先级环（右上同排） */}
      <div className="mt-3 flex flex-col gap-2.5 sm:flex-row sm:items-stretch">
        <div className="grid flex-1 grid-cols-1 gap-2.5 sm:grid-cols-3">
          <div className="rounded-md border border-line bg-card-warm px-3 py-2">
            <p className="text-micro text-ink-400">当前价</p>
            <p className="mt-0.5 flex items-center gap-2">
              <span
                className={cn(
                  'rounded-xs px-1 font-mono text-data-l text-ink-900 tnum',
                  flash === 'up' && 'animate-tick-flash-up',
                  flash === 'down' && 'animate-tick-flash-down',
                )}
              >
                {num(e.current_price) !== null ? fmtPrice(e.current_price) : '—'}
              </span>
              <ChangeBadge value={e.session_change_pct} size="sm" />
            </p>
          </div>
          <div className="rounded-md border border-line bg-card-warm px-3 py-2">
            <p className="flex items-center gap-1 text-micro text-ink-400">
              <Icon name="flag" size={11} className="text-brand-600" />
              突破枢轴
            </p>
            <p className="mt-0.5 font-mono text-data-l text-ink-900 tnum">{num(e.pivot_price) !== null ? fmtPrice(e.pivot_price) : '—'}</p>
          </div>
          <div className="rounded-md border border-line bg-card-warm px-3 py-2">
            <p className="flex items-center gap-1 text-micro text-ink-400">
              <Icon name="shield" size={11} className="text-down-600" />
              失效位置
            </p>
            <p className="mt-0.5 font-mono text-data-l text-ink-900 tnum">{invalid !== null ? fmtPrice(invalid) : '—'}</p>
          </div>
        </div>
        <div className="flex items-center justify-center rounded-md border border-line bg-card-warm px-3 py-1.5">
          <PriorityRing score={num(e.alert_priority_score) ?? 0} />
        </div>
      </div>

      {/* 生命周期步进条 */}
      <div className="mt-4 border-t border-line pt-3">
        <LifecycleStepper state={e.lifecycle_state} />
      </div>

      {/* 4 评分条 + 贡献分段条 */}
      <div className="mt-4 grid grid-cols-1 gap-4 border-t border-line pt-3 lg:grid-cols-2">
        <section aria-label="四维评分">
          <p className="eyebrow mb-2">四维评分</p>
          <BigScoreBars ev={e} />
        </section>
        <section aria-label="评分贡献" className="lg:border-l lg:border-line lg:pl-4">
          <p className="eyebrow mb-2">
            评分贡献
            <InfoHint hint={SCORE_HINTS.breakoutPriority} size={12} className="ml-1" />
          </p>
          <ContributionBar ev={e} />
        </section>
      </div>

      {/* 风险提醒 */}
      {warnings.length > 0 && (
        <div className="mt-3 flex flex-wrap gap-1.5" aria-label="风险提醒">
          {warnings.map((w, i) => (
            <span
              key={i}
              className="inline-flex items-center gap-1 rounded-xs border border-warn-600/40 bg-warn-50 px-2 py-0.5 text-micro text-warn-600"
            >
              <Icon name="flag" size={11} />
              {w}
            </span>
          ))}
        </div>
      )}

      {/* 底行：版本信息 + 操作按钮（「打开研究页」保留全屏 /stock/:t） */}
      <div className="mt-4 flex flex-wrap items-center justify-between gap-x-4 gap-y-3 border-t border-line pt-3">
        <p className="font-mono text-micro text-ink-400 tnum">
          评分 {scoreVersion} · 形态 {shapeTxt} · 观测 {observedAgo(e.event_at)}
        </p>
        <div className="flex items-center gap-2">
          <button
            onClick={() => onOpen(e)}
            className="flex items-center gap-1.5 rounded-md border border-line bg-card px-3.5 py-2 text-caption font-medium text-ink-600 transition-colors duration-fast hover:border-brand-400 hover:text-brand-600"
          >
            <Icon name="doc-quote" size={13} />
            查看完整证据
          </button>
          <Link
            to={`/stock/${encodeURIComponent(e.ticker)}`}
            className="flex items-center gap-1.5 rounded-md bg-brand-600 px-3.5 py-2 text-caption font-medium text-white transition-[filter] duration-fast hover:brightness-105"
          >
            打开研究页
            <Icon name="arrow-up-right" size={13} />
          </Link>
        </div>
      </div>
    </motion.article>
  );
}
