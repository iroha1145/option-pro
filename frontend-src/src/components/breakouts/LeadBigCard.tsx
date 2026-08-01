/**
 * LeadBigCard · 当日信号 lead 事件「压缩版大事件卡」（Paper Terminal 皮肤，7/12 宽）
 * 结构：状态 chips 行（lg 合并 meta 行）+ 「首要信号」徽章 → Serif Display-M 标题（ticker 开抽屉）
 *      → K线迷你图（ECharts 蜡烛 150px）→ 三价位行 + 优先级环（右上同排）
 *      → 生命周期步进条 → 4 评分条 + 贡献分段条 → 风险提醒 → 底部版本与按钮
 * 数据：breakouts/current 事件 + breakouts/events/{id} 详情补全（宽松字段合并，缺字段显「—」）
 * 动效：rise-in 进场 · count-up 优先级环 · grow-bar 错峰 · draw-line 环弧 · 现价 tick-flash
 *      · 步进条节点 60ms 错峰入场 + 连接线渐进填充 400ms
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
import { fmtNyEventTime, fmtNyHHmm, fmtPrice, fmtRelative } from '@/lib/format';
import { MACRO_TONE_LABEL, macroToneOf } from '@/lib/macroFit';
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
import { t } from '../../i18n/core.ts';

const EASE_PAPER = [0.16, 1, 0.3, 1] as [number, number, number, number];
const MONO = '"IBM Plex Mono", monospace';

/* ---------------- 工具 ---------------- */

/** 事件时间 M/DD HH:mm：统一 ET（lib/format 的 fmtNyEventTime）。原实现用
 *  getHours() 走浏览器本地时区、旁边却标着「美东」——UTC+8 用户看到的时刻
 *  与右侧历史轨道（HistoryRail，正确的 ET）差一整个时区。 */
const fmtEventTime = fmtNyEventTime;

/** 观测 x 小时前（不足 1 小时显分钟） */
function observedAgo(iso: string): string {
  const ms = Date.now() - new Date(iso).getTime();
  if (!Number.isFinite(ms) || ms < 0) return '—';
  const h = ms / 3_600_000;
  if (h >= 1) return t('{n} 小时前', { n: h >= 10 ? Math.round(h) : h.toFixed(1) });
  return t('{n} 分钟前', { n: Math.max(1, Math.round(ms / 60_000)) });
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

/* ---------------- 宏观影子优先级 ---------------- */
/**
 * 「如果宏观接入正式评分，这个提醒优先级会变成多少」。
 *
 * 环上的分数是生产值，这里显示的是影子值和差额，两者并排就能看出宏观改变了多少。
 * 上限 ±4，且不改动任何质量分或事件生命周期 —— 宏观逆风不会删除一个真实突破。
 *
 * 没有读数就不渲染：在这张已经很密的卡上，一个「暂无」比不显示更吵，而具体原因
 * 在个股抽屉的宏观块里说得清楚。
 */
function MacroPriorityShadow({ ev }: { ev: BreakoutEventFull }) {
  const shadow = num(ev.alert_priority_macro_shadow);
  const delta = num(ev.macro_priority_adjustment_shadow);
  const fit = num(ev.macro_fit_score);
  if (shadow === null || delta === null || fit === null) return null;
  const tone = macroToneOf(fit, ev.macro_tailwind);
  return (
    <span
      className="flex items-center gap-1 text-micro text-ink-400"
      title={t('宏观适配 {fit}（{tone}）。影子优先级 = 生产优先级 {sign} {delta}，上限 ±4。不改变突破质量分与事件生命周期。', { fit: fit.toFixed(1), tone: tone ? t(MACRO_TONE_LABEL[tone]) : '—', sign: delta >= 0 ? '+' : '−', delta: Math.abs(delta).toFixed(1) })}
    >
      <span>{t('宏观影子')}</span>
      <span className="font-mono tnum text-ink-600">{shadow.toFixed(1)}</span>
      <span
        className={cn(
          'font-mono tnum',
          delta > 0 ? 'text-up-700' : delta < 0 ? 'text-down-700' : 'text-ink-400',
        )}
      >
        {delta > 0 ? '+' : ''}{delta.toFixed(1)}
      </span>
    </span>
  );
}

/* ---------------- 告警优先级环（64px 紧凑版：轨道 line + brand-600 弧 draw-line + count-up） ---------------- */
function PriorityRing({ score }: { score: number | null }) {
  /* score=null 表示后端判定 insufficient_data（算不出来）。渲染成「0」会被
     读成「优先级极低」并据此排除信号（审计 2.1.13）——同卡其他缺失字段
     都显「—」，这里保持一致。 */
  const v = useCountUp(score ?? 0, 900);
  const r = 24;
  const c = 2 * Math.PI * r;
  const frac = score === null ? 0 : Math.max(0, Math.min(100, score)) / 100;
  return (
    <div
      className="flex shrink-0 flex-col items-center"
      aria-label={score === null ? t('告警优先级数据不足') : t('告警优先级 {score}', { score })}
    >
      <div className="relative size-[64px]">
        <svg viewBox="0 0 64 64" className="size-full -rotate-90" aria-hidden="true">
          <circle cx="32" cy="32" r={r} fill="none" stroke="var(--line)" strokeWidth="4.5" />
          {score !== null && (
            <motion.circle
              cx="32"
              cy="32"
              r={r}
              fill="none"
              stroke="var(--brand-600)"
              strokeWidth="4.5"
              strokeLinecap="round"
              strokeDasharray={c}
              initial={{ strokeDashoffset: c }}
              animate={{ strokeDashoffset: c * (1 - frac) }}
              transition={{ duration: 1.1, ease: EASE_PAPER }}
            />
          )}
        </svg>
        <div className="absolute inset-0 flex items-center justify-center">
          <span className="font-mono text-[15px] font-medium leading-[20px] text-ink-900 tnum">
            {score === null ? '—' : Math.round(v)}
          </span>
        </div>
      </div>
      <span className="mt-0.5 whitespace-nowrap text-[10px] leading-[13px] text-ink-400">
        {t('告警优先级')}
        <InfoHint hint={SCORE_HINTS.breakoutPriority} side="top" align="end" size={11} className="ml-0.5" />
      </span>
    </div>
  );
}

/* ---------------- 生命周期步进条（五节点 + FAILED/EXPIRED 末端标记） ---------------- */
const STEPS = [
  { key: 'DISCOVERED', label: t('已发现') },
  { key: 'TRIGGERED', label: t('已触发') },
  { key: 'CONFIRMED', label: t('已确认') },
  { key: 'HOLDING', label: t('保持中') },
  { key: 'RETESTING', label: t('回踩中') },
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

  /* hum-07 步骤徽章：当前态 brand 实心 + 同色系软晕；已过态实心灰蓝；未来态空心发丝圈 */
  const node = (label: string, tone: 'past' | 'current' | 'future' | 'down' | 'ink-end', key: string, order: number) => (
    <div key={key} className="flex min-w-[44px] flex-col items-center">
      <motion.span
        /* 柔和入场：节点按序 60ms 错峰 scale 0.8→1（仅首次挂载播放） */
        initial={{ opacity: 0, scale: 0.8 }}
        animate={{ opacity: 1, scale: 1 }}
        transition={{ duration: 0.3, ease: EASE_PAPER, delay: order * 0.06 }}
        className={cn(
          'size-2.5 rounded-full',
          tone === 'current' && 'bg-brand-600 ring-4 ring-brand-100',
          tone === 'past' && 'bg-ink-300',
          tone === 'future' && 'border border-line-strong bg-card',
          tone === 'down' && 'bg-down-600 ring-2 ring-down-600/25',
          tone === 'ink-end' && 'bg-ink-400 ring-2 ring-ink-400/25',
        )}
        aria-hidden="true"
      />
      <span
        className={cn(
          'mt-1.5 whitespace-nowrap text-[10px] leading-[14px]',
          tone === 'current' && 'font-semibold text-brand-700',
          (tone === 'past' || tone === 'future') && 'text-ink-400',
          tone === 'down' && 'font-semibold text-down-700',
          tone === 'ink-end' && 'font-semibold text-ink-500',
        )}
      >
        {label}
      </span>
    </div>
  );

  const items: { el: ReturnType<typeof node>; passed: boolean }[] = STEPS.map((s, i) => ({
    /* 第 5 节点聚合了 RETESTING/RETEST_HELD/REACCELERATING/EXTENDED 四个状态：
       当前节点直接显示真实状态名（LIFECYCLE_CN），不再一律标成「回踩中」。 */
    el: node(
      i === idx ? (LIFECYCLE_CN[state] ?? s.label) : s.label,
      i === idx ? 'current' : i <= pastTo ? 'past' : 'future',
      s.key,
      i,
    ),
    passed: terminal ? i < STEPS.length - 1 : i < idx,
  }));
  if (terminal) {
    items.push({
      el: node(LIFECYCLE_CN[terminal], terminal === 'FAILED' ? 'down' : 'ink-end', terminal, STEPS.length),
      passed: false,
    });
  }

  return (
    /* overflow-x-auto 会把 overflow-y 一并钳成 auto：当前态圆点贴容器顶，
       ring-4 软晕上沿会被裁平。pt-1.5 给光晕让出裁剪盒内空间，-mt-1.5 抵消外部布局。 */
    <ol className="no-scrollbar -mt-1.5 flex items-start overflow-x-auto pt-1.5" aria-label={t('生命周期：{state}', { state: LIFECYCLE_CN[state] ?? state })}>
      {items.map((it, i) => (
        <li key={i} className="flex flex-1 items-start last:flex-none">
          {it.el}
          {i < items.length - 1 && (
            /* 渐进填充连接线：轨道 line 色，已过段 brand-600 随状态迁移 400ms ease-out 推进 */
            <span className="relative mx-1 mt-[5px] h-px min-w-3 flex-1 bg-line" aria-hidden="true">
              <motion.span
                className="absolute inset-y-0 left-0 bg-brand-600"
                initial={{ width: '0%' }}
                animate={{ width: it.passed ? '100%' : '0%' }}
                transition={{ duration: 0.4, ease: 'easeOut', delay: 0.24 + i * 0.06 }}
              />
            </span>
          )}
        </li>
      ))}
    </ol>
  );
}

/* ---------------- K线迷你图（15m 蜡烛最近 96 根 · 发丝网格 · 十字光标 · 诚实空态） ---------------- */
/** mapBar 运行时携带契约 quote_only（可选字段，Candle 类型未声明，此处宽松扩展读取） */
type MiniBar = Candle & { quote_only?: boolean };

function buildMiniOption(bars: MiniBar[]): ChartOption {
  /* K 线 bar 的语义时区是美东交易时段，轴标/tooltip 与卡头事件时间同口径 */
  const labels = bars.map((b) => fmtNyHHmm(b.t));
  /** tooltip 时间：15m 跨日数据需 M/DD 日期前缀，末根 quote_only 如实标注 */
  const fmtBarTime = (iso: string) => fmtNyEventTime(iso);
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
          /* DOM 渲染，跟随 --line 令牌 */
          borderColor: 'var(--line)',
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
        return (
          `<div style="font-family:${MONO};font-size:12px;line-height:19px">` +
          `<div style="color:#8A94B0">${fmtBarTime(b.t)}${b.quote_only ? t(' · 仅报价') : ''}</div>` +
          `${t('开 {o}', { o: b.o.toFixed(2) })} · ${t('高 {h}', { h: b.h.toFixed(2) })}<br/>${t('低 {l}', { l: b.l.toFixed(2) })} · ` +
          `${t('收 {c}', { c: `<b style="color:${color}">${b.c.toFixed(2)}</b>` })}</div>`
        );
      },
    }),
    series: [
      {
        type: 'candlestick' as const,
        /* 末根常为「仅报价」实时 bar（quote_only）：半透明如实标注，不当成已收齐 K 线 */
        data: bars.map((b) =>
          b.quote_only
            ? { value: [b.o, b.c, b.l, b.h], itemStyle: { opacity: 0.45 } }
            : [b.o, b.c, b.l, b.h],
        ),
        itemStyle: {
          color: CH.up600,
          color0: CH.down600,
          borderColor: CH.up600,
          borderColor0: CH.down600,
          borderWidth: 1,
        },
        barMaxWidth: 10,
        z: 3,
      },
    ],
  } as ChartOption;
}

function MiniKline({ ticker }: { ticker: string }) {
  /* 15m 周期（与 StockChart['range'] 类型一致）+ 截取最近 96 根展示 */
  const { data, error, loading, refresh } = usePolling(() => stocksApi.chart(ticker, '15m'), null, [ticker]);
  const option = useMemo(() => {
    if (!data || data.candles.length <= 1) return null;
    return buildMiniOption(data.candles.slice(-96));
  }, [data]);
  /* 突破标的常不在常规覆盖范围内：503 时可手动拉取（与详情页 ManualStockPull 同一预算通道） */
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
          ? cause.bizCode === 'owner_login_required'
            ? t('拉取需要 Owner 登录；访客只读已保存的快照')
            : `${cause.message}${cause.retryAfter ? t(' · {n} 秒后可重试', { n: cause.retryAfter }) : ''}`
          : t('拉取失败，请稍后重试'),
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
            {snapshotMissing ? t('暂无当日 K 线') : t('K 线读取失败')}
          </p>
          <p className="text-micro text-ink-400">
            {snapshotMissing ? t('可点击下方按钮获取最新行情') : t('分时行情暂不可用')}
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
                className="inline-flex items-center gap-1.5 rounded-md bg-brand-600 px-2.5 py-1 text-micro font-medium text-white shadow-btn-hi transition-[background-color,opacity] duration-fast hover:bg-brand-700 disabled:cursor-wait disabled:opacity-70"
              >
                {pulling && (
                  <span
                    className="size-2.5 animate-spin rounded-full border-2 border-white/35 border-t-white"
                    aria-hidden="true"
                  />
                )}
                {pulling ? t('正在拉取') : t('拉取行情')}
              </button>
            )}
            <button
              onClick={refresh}
              className="rounded-md border border-line bg-card px-2.5 py-1 text-micro font-medium text-ink-600 shadow-btn transition-colors duration-fast hover:border-brand-400 hover:text-brand-600"
            >
              {t('重试')}
            </button>
          </div>
        </div>
      ) : (
        <ReactECharts option={option} ariaLabel={t('{ticker} 15 分钟迷你 K 线图', { ticker })} />
      )}
    </div>
  );
}

/* ---------------- 四维评分条（grow-bar 错峰 + Mono 值） ---------------- */
const BIG_SCORES = [
  { key: 'base_quality_score', label: t('突破质量'), hint: SCORE_HINTS.breakoutBase },
  { key: 'breakout_confirmation_score', label: t('确认强度'), hint: SCORE_HINTS.breakoutConfirmation },
  { key: 'data_confidence_score', label: t('数据可信度'), hint: SCORE_HINTS.breakoutDataConfidence },
  { key: 'chase_risk_score', label: t('追高风险'), hint: SCORE_HINTS.breakoutChaseRisk },
] as const;

function BigScoreBars({ ev }: { ev: BreakoutEventFull }) {
  return (
    <div className="grid grid-cols-[max-content_minmax(0,1fr)_max-content] gap-y-2" aria-label={t("四维评分")}>
      {BIG_SCORES.map((d, i) => {
        const raw = num(ev[d.key]);
        return (
          <div key={d.key} className="col-span-3 grid grid-cols-subgrid items-center gap-x-2.5">
            <span className="whitespace-nowrap text-caption text-ink-500">
              {d.label}
              <InfoHint hint={d.hint} size={11} className="ml-0.5" />
            </span>
            <div className="h-[3px] overflow-hidden rounded-pill bg-line">
              {/* 缺失值保持空轨道，与 ScoreBars 的 fin(v) 口径一致（审计 2.2.15）：
                * 3% 的实心条会被读成「有分，只是很低」，与右侧的「—」矛盾。 */}
              {raw !== null && (
                <motion.div
                  className={cn('h-full origin-left rounded-pill', d.key === 'chase_risk_score' ? riskBarClass(raw) : scoreBarClass(raw))}
                  initial={{ scaleX: 0 }}
                  whileInView={{ scaleX: 1 }}
                  viewport={{ once: true, amount: 0.4 }}
                  transition={{ duration: 0.7, ease: EASE_PAPER, delay: 0.1 + i * 0.06 }}
                  style={{ width: `${Math.max(3, Math.min(100, raw))}%` }}
                />
              )}
            </div>
            <span className="text-right font-mono text-caption text-ink-600 tnum">{raw !== null ? raw.toFixed(1) : '—'}</span>
          </div>
        );
      })}
    </div>
  );
}

/* ---------------- 贡献分段条（5 段一整条 + 图例；无 contribution_breakdown 时按权重×评分推导） ---------------- */
/* 分段只信后端 contribution_breakdown（含 event_freshness）；没有它就不画。
   旧实现用一套与后端不一致的前端权重（0.30/0.25/0.15/0.15/0.15，缺
   event_freshness，板块契合/数据置信高估、突破质量低估）把缺失推导成
   五段 0.0（审计 2.1.14）——那不是「推导」，是错误口径的编造。 */
const CONTRIB_DEFS = [
  { key: 'breakout_quality', label: t('突破质量'), cls: 'bg-brand-600' },
  { key: 'intrinsic_strength', label: t('内在强度'), cls: 'bg-brand-400' },
  { key: 'market_fit', label: t('市场契合'), cls: 'bg-ai-600' },
  { key: 'sector_fit', label: t('板块契合'), cls: 'bg-up-600' },
  { key: 'data_confidence', label: t('数据置信'), cls: 'bg-ink-300' },
  { key: 'event_freshness', label: t('事件新鲜度'), cls: 'bg-warn-600' },
] as const;

function ContributionBar({ ev }: { ev: BreakoutEventFull }) {
  const parts = useMemo(() => {
    const loose = (ev as unknown as { contribution_breakdown?: Record<string, unknown> }).contribution_breakdown;
    if (!loose || typeof loose !== 'object') return null;
    const raws = CONTRIB_DEFS.flatMap((d) => {
      const v = num(loose[d.key]);
      return v !== null ? [{ d, v, pct: 0 }] : [];
    });
    if (raws.length === 0) return null;
    const total = raws.reduce((s, p) => s + p.v, 0) || 1;
    return raws.map((p) => ({ ...p, pct: (p.v / total) * 100 }));
  }, [ev]);

  if (parts === null) {
    return (
      <p className="text-[11px] leading-[14px] text-ink-400">
        {t('评分构成不可用（数据不足）')}
      </p>
    );
  }

  return (
    <div aria-label={t("评分贡献分解")}>
      <div className="flex h-[3px] overflow-hidden rounded-pill bg-line">
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
  /* 交易所缺失时整项隐藏（不再显「—」占位） */
  const exchange = str(loose.exchange);
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
      aria-label={t('{ticker} {setup} 首要信号大卡', { ticker: e.ticker, setup: SETUP_CN[e.setup_type] ?? e.setup_type ?? '' })}
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
        {/* lg 以上：meta 并入 chips 行（空格分隔 inline 项，不再用 · 串） */}
        <span className="hidden items-center gap-3 text-micro text-ink-500 lg:inline-flex">
          {exchange && <span>{exchange}</span>}
          <span>{e.sector}</span>
          <span className="font-mono tnum">{t('跳空')} {gap !== null ? `${gap >= 0 ? '+' : ''}${gap.toFixed(2)}%` : '—'}</span>
          <span className="font-mono tnum">{t('量能')} {rvol !== null ? `${rvol.toFixed(1)}×` : '—'}</span>
          <span className="font-mono tnum">{fmtEventTime(e.event_at)} {t('美东')}</span>
        </span>
        <span className="ml-auto inline-flex items-center gap-1.5 rounded-pill border border-brand-400/50 bg-brand-50 px-2.5 py-1 text-micro font-medium text-brand-600">
          <Icon name="radar" size={12} />
          {t('首要信号')}
        </span>
      </div>

      {/* 标题区（ticker 点击开右侧抽屉，保留雷达上下文） */}
      <div className="mt-2.5">
        <h3 className="font-display text-display-m text-ink-900">
          {e.name}{' '}
          <button
            onClick={() => openTicker(e.ticker)}
            aria-label={t('打开 {ticker} 个股详情抽屉', { ticker: e.ticker })}
            className="text-brand-600 underline-offset-4 transition-colors hover:text-brand-700 hover:underline"
          >
            {e.ticker}
          </button>
        </h3>
        <p className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-caption text-ink-500 lg:hidden">
          {exchange && <span>{exchange}</span>}
          <span>{e.sector}</span>
          <span className="font-mono tnum">{t('跳空')} {gap !== null ? `${gap >= 0 ? '+' : ''}${gap.toFixed(2)}%` : '—'}</span>
          <span className="font-mono tnum">{t('同时段量能')} {rvol !== null ? `${rvol.toFixed(1)}×` : '—'}</span>
          <span className="font-mono tnum">{t('事件时间')} {fmtEventTime(e.event_at)} {t('美东')}</span>
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
            <p className="text-micro text-ink-400">{t('当前价')}</p>
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
              {t('突破枢轴')}
            </p>
            <p className="mt-0.5 font-mono text-data-l text-ink-900 tnum">{num(e.pivot_price) !== null ? fmtPrice(e.pivot_price) : '—'}</p>
          </div>
          <div className="rounded-md border border-line bg-card-warm px-3 py-2">
            <p className="flex items-center gap-1 text-micro text-ink-400">
              <Icon name="shield" size={11} className="text-down-600" />
              {t('失效位置')}
            </p>
            <p className="mt-0.5 font-mono text-data-l text-ink-900 tnum">{invalid !== null ? fmtPrice(invalid) : '—'}</p>
          </div>
        </div>
        <div className="flex flex-col items-center justify-center gap-1.5 rounded-md border border-line bg-card-warm px-3 py-1.5">
          <PriorityRing score={num(e.alert_priority_score)} />
          {/* 宏观影子：显示的是「如果接入，优先级会变成多少」，环上的分数不变。
              上限 ±4；突破质量、确认、流动性、追高风险和事件生命周期一律不动。 */}
          <MacroPriorityShadow ev={e} />
        </div>
      </div>

      {/* 生命周期步进条 */}
      <div className="mt-4 border-t border-line pt-3">
        <LifecycleStepper state={e.lifecycle_state} />
      </div>

      {/* 4 评分条 + 贡献分段条 */}
      <div className="mt-4 grid grid-cols-1 gap-4 border-t border-line pt-3 lg:grid-cols-2">
        <section aria-label={t("四维评分")}>
          <p className="eyebrow mb-2">{t('四维评分')}</p>
          <BigScoreBars ev={e} />
        </section>
        <section aria-label={t("评分贡献")} className="lg:border-l lg:border-line lg:pl-4">
          <p className="eyebrow mb-2">
            {t('评分贡献')}
            <InfoHint hint={SCORE_HINTS.breakoutPriority} size={12} className="ml-1" />
          </p>
          <ContributionBar ev={e} />
        </section>
      </div>

      {/* 风险提醒 */}
      {warnings.length > 0 && (
        <div className="mt-3 flex flex-wrap gap-1.5" aria-label={t("风险提醒")}>
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
          {t('评分')} {scoreVersion} {t('· 形态')} {shapeTxt} {t('· 观测')} {observedAgo(e.event_at)}
        </p>
        <div className="flex items-center gap-2">
          <button
            onClick={() => onOpen(e)}
            className="flex items-center gap-1.5 rounded-md border border-line bg-card px-3.5 py-2 text-caption font-medium text-ink-600 shadow-btn transition-colors duration-fast hover:border-brand-400 hover:text-brand-600"
          >
            <Icon name="doc-quote" size={13} />
            {t('查看完整证据')}
          </button>
          <Link
            to={`/stock/${encodeURIComponent(e.ticker)}`}
            className="flex items-center gap-1.5 rounded-md bg-brand-600 px-3.5 py-2 text-caption font-medium text-white shadow-btn-hi transition-[transform,background-color] duration-fast hover:bg-brand-700 active:scale-[0.98]"
          >
            {t('打开研究页')}
            <Icon name="arrow-up-right" size={13} />
          </Link>
        </div>
      </div>
    </motion.article>
  );
}
