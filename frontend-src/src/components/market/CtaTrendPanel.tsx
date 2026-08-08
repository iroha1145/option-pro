/**
 * CTA 趋势资金估算卡（/market B4.5）
 *
 * 语义纪律（与后端 cta-proxy 模型一一对应）：
 * - 这是**代理估算**：多周期趋势模型群的机械仓位，不是任何机构的真实仓位
 *   披露，也不输出美元流量——文案与标签一律用「估算/代理/模型触发位」。
 * - 「仓位」与「资金流」分开呈现：净多但在减仓 ≠ 翻空；波动率去杠杆
 *   （trend_flow / volatility_flow 拆分）单独标注。
 * - 触发位全部「需收盘确认」；盘中穿越只挂暂定章。
 * - 数据缺失显示诚实空态，不折中性值；快照未发布 → 说明 + 等 worker。
 * - 不改变 market_regime / Strength 排名；与市场环境只做并排联动解释。
 */
import { useMemo, useState } from 'react';
import ReactECharts from '@/components/charts/ReactECharts';
import Segmented from '@/components/shared/Segmented';
import InfoHint from '@/components/shared/InfoHint';
import { SkeletonBlock } from '@/components/shared/Skeleton';
import Icon from '@/components/icons';
import { CTA_HINTS } from '@/lib/ctaHints';
import { baseAnimation, CH, glassTooltip, type ChartOption } from '@/lib/chart';
import { fmtPrice } from '@/lib/format';
import { cn } from '@/lib/utils';
import type { ApiError } from '@/api/client';
import type { CtaInstrumentEstimate, CtaTrendPayload, CtaTriggerZone } from '@/api/types';
import { t } from '../../i18n/core.ts';

/* 定义处 t()（RESULT_META 惯例）；渲染处直接用，不二次翻译 */
const POSITION_META: Record<string, { label: string; cls: string }> = {
  strong_long: { label: t('强净多'), cls: 'bg-up-50 text-up-700' },
  net_long: { label: t('净多'), cls: 'bg-up-50 text-up-700' },
  divergent: { label: t('模型分歧'), cls: 'bg-warn-50 text-warn-600' },
  neutral: { label: t('中性'), cls: 'bg-paper-2 text-ink-600' },
  net_short: { label: t('净空'), cls: 'bg-down-50 text-down-700' },
  strong_short: { label: t('强净空'), cls: 'bg-down-50 text-down-700' },
};

const FLOW_META: Record<string, string> = {
  long_add: t('多头加仓'),
  long_trim: t('多头减仓'),
  short_add: t('空头加仓'),
  short_cover: t('空头回补'),
  rebuilding: t('重新建多'),
  reducing: t('转向减持'),
  steady: t('边际持稳'),
};

const ZONE_LABELS: Record<string, string> = {
  short_cover: t('空头回补'),
  reopen_long: t('重新加多'),
  add_long: t('恢复加仓'),
  buy_accelerate: t('买盘加速'),
  add_further: t('进一步加仓'),
  trim_long: t('多头减仓'),
  reopen_short: t('重新加空'),
  add_short: t('继续加空'),
  sell_accelerate: t('卖盘加速'),
  flip_further: t('进一步翻空'),
};

const ZONE_KIND: Record<string, string> = {
  trend_flip: t('趋势模型翻转'),
  vol_delever: t('波动率去杠杆'),
  mixed: t('趋势+波动率'),
};

const MODEL_SHORT: Record<string, string> = {
  fast: t('快'),
  medium: t('中'),
  slow: t('慢'),
};

const signed = (v: number | null, digits = 1): string =>
  v === null ? '—' : `${v > 0 ? '+' : ''}${v.toFixed(digits)}`;

function PositionBar({ value }: { value: number }) {
  const half = Math.min(100, Math.abs(value)) / 2;
  const isLong = value >= 0;
  return (
    <div className="relative h-[6px] w-full overflow-hidden rounded-pill bg-line" role="presentation">
      <span className="absolute inset-y-0 left-1/2 w-px bg-ink-300" aria-hidden />
      <span
        className={cn('absolute inset-y-0 rounded-pill', isLong ? 'bg-up-600' : 'bg-down-600')}
        style={isLong ? { left: '50%', width: `${half}%` } : { right: '50%', width: `${half}%` }}
      />
    </div>
  );
}

function SignalRow({ label, signal, hint }: { label: string; signal: number; hint?: typeof CTA_HINTS[string] }) {
  const half = Math.min(1, Math.abs(signal)) * 50;
  return (
    <div className="flex items-center gap-2">
      <span className="flex w-28 shrink-0 items-center gap-1 text-micro text-ink-500">
        {label}
        {hint && <InfoHint hint={hint} size={10} />}
      </span>
      <div className="relative h-[5px] flex-1 overflow-hidden rounded-pill bg-line" role="presentation">
        <span className="absolute inset-y-0 left-1/2 w-px bg-ink-300" aria-hidden />
        <span
          className={cn('absolute inset-y-0 rounded-pill', signal >= 0 ? 'bg-up-600' : 'bg-down-600')}
          style={signal >= 0 ? { left: '50%', width: `${half}%` } : { right: '50%', width: `${half}%` }}
        />
      </div>
      <span className="w-12 shrink-0 text-right font-mono text-micro text-ink-700 tnum">
        {signed(signal, 2)}
      </span>
    </div>
  );
}

function ZoneRow({ zone, side, crossed }: { zone: CtaTriggerZone; side: 'above' | 'below'; crossed: boolean }) {
  const tone = side === 'above' ? 'text-up-700' : 'text-down-700';
  return (
    <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5 rounded-md bg-paper-2 px-2.5 py-1.5">
      <span className={cn('text-caption font-medium', tone)}>{ZONE_LABELS[zone.label_key] ?? zone.label_key}</span>
      <span className="font-mono text-caption text-ink-800 tnum">
        {fmtPrice(zone.price_low)} – {fmtPrice(zone.price_high)}
      </span>
      <span className="font-mono text-micro text-ink-400 tnum">
        {zone.distance_pct > 0 ? '+' : ''}{zone.distance_pct.toFixed(1)}%
      </span>
      <span className={cn('font-mono text-micro tnum', tone)}>
        {t('估算 Δ{v}', { v: signed(zone.est_position_change) })}
      </span>
      <span className="rounded-pill border border-line bg-card px-1.5 py-0.5 text-micro text-ink-500">
        {ZONE_KIND[zone.kind]}
      </span>
      <span className="text-micro text-ink-400">
        {zone.models.map((m) => MODEL_SHORT[m] ?? m).join('/')} · {t('权重 {w}%', { w: Math.round(zone.weight_share * 100) })}
      </span>
      {crossed && (
        <span className="rounded-pill bg-warn-50 px-1.5 py-0.5 text-micro text-warn-600">
          {t('盘中已穿越 · 待收盘确认')}
        </span>
      )}
    </div>
  );
}

function scenarioOption(row: CtaInstrumentEstimate): ChartOption | null {
  const curve = row.scenario_curve;
  if (!curve || curve.prices.length === 0 || row.reference_price === null) return null;
  const refIndex = curve.prices.reduce(
    (best, p, i) => (Math.abs(p - row.reference_price!) < Math.abs(curve.prices[best] - row.reference_price!) ? i : best),
    0,
  );
  const zones = [...(row.trigger_levels?.above ?? []), ...(row.trigger_levels?.below ?? [])];
  const idxOf = (price: number) =>
    curve.prices.reduce((best, p, i) => (Math.abs(p - price) < Math.abs(curve.prices[best] - price) ? i : best), 0);
  return {
    ...baseAnimation,
    grid: { left: 8, right: 14, top: 12, bottom: 4, containLabel: true },
    xAxis: {
      type: 'category' as const,
      data: curve.prices.map((p) => fmtPrice(p)),
      axisLine: { show: false },
      axisTick: { show: false },
      axisLabel: { color: CH.ink400, fontSize: 10, fontFamily: '"IBM Plex Mono", monospace', interval: 23 },
    },
    yAxis: {
      type: 'value' as const,
      min: -100,
      max: 100,
      position: 'right' as const,
      axisLine: { show: false },
      axisTick: { show: false },
      axisLabel: { color: CH.ink400, fontSize: 10, fontFamily: '"IBM Plex Mono", monospace' },
      splitLine: { lineStyle: { color: CH.lineChart, width: 1 } },
    },
    tooltip: glassTooltip({
      trigger: 'axis',
      formatter: (params: unknown) => {
        const arr = params as { dataIndex: number; seriesName: string; data: number }[];
        if (!arr.length) return '';
        const idx = arr[0].dataIndex;
        const rows = arr
          .map((s) => `<div>${s.seriesName}: <b>${s.data > 0 ? '+' : ''}${s.data}</b></div>`)
          .join('');
        return (
          `<div style="font-family:'IBM Plex Mono',monospace;font-size:11px;line-height:17px">` +
          `<div style="color:#8A94B0">${t('若收于 {p}', { p: fmtPrice(curve.prices[idx]) })}</div>${rows}</div>`
        );
      },
    }),
    series: [
      {
        type: 'line' as const,
        name: t('完整敞口'),
        data: curve.full,
        showSymbol: false,
        lineStyle: { color: CH.brand500, width: 1.8 },
        markLine: {
          symbol: 'none',
          silent: true,
          data: [
            {
              xAxis: refIndex,
              lineStyle: { color: CH.ink400, width: 1, type: [4, 4] as number[] },
              label: {
                formatter: t('现价 {p}', { p: fmtPrice(row.reference_price) }),
                color: CH.ink400,
                fontSize: 10,
                fontFamily: '"IBM Plex Mono", monospace',
                position: 'insideEndTop' as const,
              },
            },
          ],
        },
        markArea: zones.length
          ? {
              silent: true,
              data: zones.map((zone) => [
                {
                  xAxis: idxOf(zone.price_low),
                  itemStyle: {
                    color: zone.est_position_change >= 0 ? CH.up600 : CH.down600,
                    opacity: 0.08,
                  },
                },
                { xAxis: idxOf(zone.price_high) },
              ]),
            }
          : undefined,
        z: 3,
      },
      {
        type: 'line' as const,
        name: t('仅趋势（波动率冻结）'),
        data: curve.trend_only,
        showSymbol: false,
        lineStyle: { color: CH.ink400, width: 1.2, type: [5, 4] as number[] },
        z: 2,
      },
    ],
  } as ChartOption;
}

export default function CtaTrendPanel({
  data,
  loading,
  error,
  onRetry,
  refreshing,
  regimeMean,
}: {
  data: CtaTrendPayload | null | undefined;
  loading: boolean;
  error: ApiError | null;
  onRetry: () => void;
  refreshing: boolean;
  /** 形态六维均值：仅用于并排联动解释，绝不合入 CTA 读数 */
  regimeMean: number | null;
}) {
  const [instrument, setInstrument] = useState('sp500');
  const rows = useMemo(() => data?.instruments ?? [], [data]);
  const row = rows.find((item) => item.instrument === instrument) ?? rows[0] ?? null;
  const option = useMemo(() => (row ? scenarioOption(row) : null), [row]);
  const snapshotMissing = !data && error?.bizCode === 'public_snapshot_unavailable';

  const linkage = useMemo(() => {
    if (!row || regimeMean === null || row.flow_score === null || row.position_score === null) return null;
    if (regimeMean >= 55 && row.flow_score <= -3) {
      return t('市场环境偏强，但 CTA 代理正在减仓（趋势流 {tf} / 波动率流 {vf}）——机械去杠杆不等于观点转空', {
        tf: signed(row.trend_flow), vf: signed(row.volatility_flow),
      });
    }
    if (regimeMean <= 45 && row.flow_score >= 3) {
      return t('市场环境偏弱，但 CTA 代理在回补/加仓（趋势流 {tf} / 波动率流 {vf}）——趋势模型的机械变化，非环境判断', {
        tf: signed(row.trend_flow), vf: signed(row.volatility_flow),
      });
    }
    return null;
  }, [row, regimeMean]);

  return (
    <div className="card-surface p-5">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <p className="eyebrow">CTA TREND FLOW · PROXY</p>
          <h3 className="mt-1.5 flex items-center gap-1.5 text-h3 text-ink-900">
            {t('CTA 趋势资金估算')}
            <InfoHint hint={CTA_HINTS.overview} />
          </h3>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <span className="rounded-pill border border-line bg-paper-2 px-2 py-0.5 text-micro text-ink-500">
            {t('基于 ETF 趋势的代理估算')}
          </span>
          {rows.length > 1 && (
            <Segmented
              options={rows.map((item) => ({ value: item.instrument, label: t(item.label) }))}
              value={row?.instrument ?? instrument}
              onChange={setInstrument}
              className="[&_button]:text-micro"
            />
          )}
        </div>
      </div>

      {loading && !data ? (
        <div className="mt-4 space-y-2" aria-hidden="true">
          <SkeletonBlock className="h-16 w-full" />
          <SkeletonBlock className="h-40 w-full" />
        </div>
      ) : snapshotMissing ? (
        <p className="mt-4 rounded-md border border-line bg-card-warm px-3 py-4 text-caption text-ink-500">
          {t('CTA 估算快照尚未发布：Worker 完成首次计算后自动出现，无需手动操作')}
        </p>
      ) : error && !data ? (
        <div className="mt-4 flex items-center gap-3">
          <p className="text-caption text-ink-500">{t('CTA 估算读取失败')}</p>
          <button
            type="button"
            onClick={onRetry}
            disabled={refreshing}
            className="inline-flex items-center gap-1 rounded-md border border-line px-2.5 py-1 text-caption text-ink-600 hover:bg-paper-2 disabled:opacity-60"
          >
            <Icon name="refresh" size={12} />
            {t('重试')}
          </button>
        </div>
      ) : !row ? (
        <p className="mt-4 text-caption text-ink-400">{t('暂无数据')}</p>
      ) : row.source_status !== 'active' || row.position_score === null ? (
        <div className="mt-4 rounded-md border border-line bg-card-warm px-3 py-4">
          <p className="text-caption text-ink-600">
            {row.source_status === 'insufficient_data'
              ? t('{proxy} 历史长度不足（{bars}/{req} 根），未生成估算——不以中性值代替', {
                  proxy: row.proxy_symbol,
                  bars: row.coverage?.bars ?? 0,
                  req: row.coverage?.required ?? 0,
                })
              : t('{proxy} 代理数据暂不可用', { proxy: row.proxy_symbol })}
          </p>
        </div>
      ) : (
        <>
          {/* 摘要 */}
          <div className="mt-4 grid grid-cols-2 gap-3 lg:grid-cols-4">
            <div className="rounded-md bg-paper-2 px-3 py-2">
              <p className="text-micro text-ink-400">{t('当前状态')}</p>
              <div className="mt-1 flex flex-wrap items-center gap-1.5">
                {row.position_label && POSITION_META[row.position_label] && (
                  <span className={cn('rounded-pill px-2 py-0.5 text-caption font-medium', POSITION_META[row.position_label].cls)}>
                    {POSITION_META[row.position_label].label}
                  </span>
                )}
                {row.state && (
                  <span className="rounded-pill border border-line bg-card px-2 py-0.5 text-micro text-ink-600">
                    {FLOW_META[row.state] ?? row.state}
                  </span>
                )}
              </div>
            </div>
            <div className="rounded-md bg-paper-2 px-3 py-2">
              <p className="flex items-center gap-1 text-micro text-ink-400">
                {t('估算目标仓位')}
                <InfoHint hint={CTA_HINTS.position} size={10} />
              </p>
              <p className="mt-0.5 font-mono text-body font-semibold text-ink-900 tnum">
                {signed(row.position_score)}
                <span className="ml-1 text-micro font-normal text-ink-400">
                  {t('前值 {v}', { v: signed(row.previous_position_score) })}
                </span>
              </p>
              <div className="mt-1"><PositionBar value={row.position_score} /></div>
            </div>
            <div className="rounded-md bg-paper-2 px-3 py-2">
              <p className="flex items-center gap-1 text-micro text-ink-400">
                {t('今日边际流')}
                <InfoHint hint={CTA_HINTS.flow} size={10} />
              </p>
              <p className={cn('mt-0.5 font-mono text-body font-semibold tnum', (row.flow_score ?? 0) >= 0 ? 'text-up-700' : 'text-down-700')}>
                {signed(row.flow_score)}
              </p>
              <p className="font-mono text-micro text-ink-400 tnum">
                {t('趋势 {a} · 波动率 {b}', { a: signed(row.trend_flow), b: signed(row.volatility_flow) })}
              </p>
            </div>
            <div className="rounded-md bg-paper-2 px-3 py-2">
              <p className="flex items-center gap-1 text-micro text-ink-400">
                {t('模型一致度')}
                <InfoHint hint={CTA_HINTS.agreement} size={10} />
              </p>
              <p className="mt-0.5 font-mono text-body font-semibold text-ink-900 tnum">
                {row.model_agreement !== null ? `${Math.round(row.model_agreement * 100)}%` : '—'}
              </p>
              <p className="font-mono text-micro text-ink-400 tnum">
                {t('波动缩放 ×{s}', { s: row.volatility ? row.volatility.scalar.toFixed(2) : '—' })}
              </p>
            </div>
          </div>

          {/* 三速分解 + 触发阶梯 + 情景图 */}
          <div className="mt-4 grid grid-cols-1 gap-4 lg:grid-cols-12">
            <div className="space-y-1.5 lg:col-span-4">
              <p className="flex items-center gap-1 text-micro text-ink-400">
                {t('模型分解')}
                <InfoHint hint={CTA_HINTS.submodels} size={10} />
              </p>
              {row.submodels && (['fast', 'medium', 'slow'] as const).map((key) => (
                row.submodels![key] && (
                  <SignalRow key={key} label={t(row.submodels![key].label)} signal={row.submodels![key].signal} />
                )
              ))}
              {row.volatility && (
                <p className="pt-1 text-micro text-ink-400">
                  {t('已实现波动 {rv}% · 目标 {tv}% → 缩放 ×{s}', {
                    rv: row.volatility.realized_annual !== null ? (row.volatility.realized_annual * 100).toFixed(1) : '—',
                    tv: (row.volatility.target_annual * 100).toFixed(0),
                    s: row.volatility.scalar.toFixed(2),
                  })}
                </p>
              )}

              <p className="flex items-center gap-1 pt-2 text-micro text-ink-400">
                {t('模型触发位（需收盘确认）')}
                <InfoHint hint={CTA_HINTS.triggers} size={10} />
              </p>
              <div className="space-y-1.5">
                {(row.trigger_levels?.above ?? []).slice().reverse().map((zone) => (
                  <ZoneRow
                    key={zone.id}
                    zone={zone}
                    side="above"
                    crossed={row.intraday?.crossed_zone_ids.includes(zone.id) ?? false}
                  />
                ))}
                <div className="flex items-center gap-2 px-1">
                  <span className="h-px flex-1 bg-ink-300" aria-hidden />
                  <span className="font-mono text-micro text-ink-500 tnum">
                    {t('现价 {p}', { p: row.reference_price !== null ? fmtPrice(row.reference_price) : '—' })}
                  </span>
                  <span className="h-px flex-1 bg-ink-300" aria-hidden />
                </div>
                {(row.trigger_levels?.below ?? []).map((zone) => (
                  <ZoneRow
                    key={zone.id}
                    zone={zone}
                    side="below"
                    crossed={row.intraday?.crossed_zone_ids.includes(zone.id) ?? false}
                  />
                ))}
                {!(row.trigger_levels?.above?.length || row.trigger_levels?.below?.length) && (
                  <p className="text-micro text-ink-400">{t('±12% 情景范围内没有会显著改变目标仓位的价格')}</p>
                )}
              </div>
            </div>
            <div className="lg:col-span-8">
              <p className="flex items-center gap-1 text-micro text-ink-400">
                {t('情景曲线：若明日收于横轴价格，估算目标仓位为纵轴值')}
                <InfoHint hint={CTA_HINTS.scenario} size={10} />
              </p>
              {option ? (
                <div className="mt-1.5 h-56">
                  <ReactECharts option={option} ariaLabel={t('CTA 情景曲线')} />
                </div>
              ) : (
                <p className="mt-2 text-caption text-ink-400">{t('暂无数据')}</p>
              )}
              <p className="mt-1 flex flex-wrap items-center gap-x-3 text-micro text-ink-400">
                <span className="inline-flex items-center gap-1">
                  <span className="inline-block h-0 w-4 border-t-2 border-brand-500" aria-hidden />
                  {t('完整敞口（含波动率调整）')}
                </span>
                <span className="inline-flex items-center gap-1">
                  <span className="inline-block h-0 w-4 border-t border-dashed border-ink-400" aria-hidden />
                  {t('仅趋势（波动率冻结）')}
                </span>
              </p>
            </div>
          </div>

          {linkage && (
            <p className="mt-3 rounded-md bg-ai-50 px-3 py-2 text-caption text-ai-600">{linkage}</p>
          )}

          <p className="mt-3 text-micro text-ink-400">
            {t('数据截至 {d} 收盘', { d: row.data_through ?? '—' })}
            {row.intraday?.provisional && <span> · {t('盘中读数为暂定，不入正式历史')}</span>}
            {' · '}{t('方法 {v} · 代理={p}', { v: data?.method_version ?? '—', p: row.proxy_symbol })}
            {' · '}{t('代理模型估算，非任何机构真实仓位披露')}
          </p>
        </>
      )}
    </div>
  );
}
