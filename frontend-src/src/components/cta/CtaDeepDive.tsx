/**
 * 单标的深读主面板（/cta B2）。
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
import { useMemo } from 'react';
import { motion } from 'framer-motion';
import Segmented from '@/components/shared/Segmented';
import InfoHint from '@/components/shared/InfoHint';
import { CTA_HINTS } from '@/lib/ctaHints';
import { cn } from '@/lib/utils';
import type { CtaInstrumentEstimate, CtaTrendPayload } from '@/api/types';
import { t } from '../../i18n/core.ts';
import { FLOW_META, POSITION_META, signed } from './ctaMeta';
import PositionHistoryChart from './PositionHistoryChart';
import ScenarioChart from './ScenarioChart';
import TriggerLadder from './TriggerLadder';

/** −100~+100 动画仓位条（framer 全局遵守系统 reducedMotion） */
function PositionBar({ value }: { value: number }) {
  const half = Math.min(100, Math.abs(value)) / 2;
  const isLong = value >= 0;
  return (
    <div className="relative h-[6px] w-full overflow-hidden rounded-pill bg-line" role="presentation">
      <span className="absolute inset-y-0 left-1/2 w-px bg-ink-300" aria-hidden />
      <motion.span
        className={cn('absolute inset-y-0 rounded-pill', isLong ? 'bg-up-600' : 'bg-down-600')}
        initial={false}
        animate={{ left: `${isLong ? 50 : 50 - half}%`, width: `${half}%` }}
        transition={{ duration: 0.5, ease: [0.16, 1, 0.3, 1] }}
      />
    </div>
  );
}

/** 「x/y 同向」：表态（|signal|>0.1）子模型里与总趋势同号的个数。 */
function directionCount(row: CtaInstrumentEstimate): string {
  const subs = row.submodels ? Object.values(row.submodels) : [];
  const active = subs.filter((s) => Math.abs(s.signal) > 0.1);
  if (!active.length) return '0/0';
  const dir = (row.trend_strength ?? row.position_score ?? 0) >= 0 ? 1 : -1;
  const agree = active.filter((s) => s.signal * dir > 0).length;
  return `${agree}/${active.length}`;
}

function SignalRow({ label, signal }: { label: string; signal: number }) {
  const half = Math.min(1, Math.abs(signal)) * 50;
  return (
    <div className="flex items-center gap-2">
      <span className="w-28 shrink-0 text-micro text-ink-500">{label}</span>
      <div className="relative h-[5px] flex-1 overflow-hidden rounded-pill bg-line" role="presentation">
        <span className="absolute inset-y-0 left-1/2 w-px bg-ink-300" aria-hidden />
        <motion.span
          className={cn('absolute inset-y-0 rounded-pill', signal >= 0 ? 'bg-up-600' : 'bg-down-600')}
          initial={false}
          animate={{ left: `${signal >= 0 ? 50 : 50 - half}%`, width: `${half}%` }}
          transition={{ duration: 0.5, ease: [0.16, 1, 0.3, 1] }}
        />
      </div>
      <span className="w-12 shrink-0 text-right font-mono text-micro text-ink-700 tnum">
        {signed(signal, 2)}
      </span>
    </div>
  );
}

export default function CtaDeepDive({
  data,
  rows,
  row,
  onInstrumentChange,
  regimeMean,
}: {
  data: CtaTrendPayload;
  rows: CtaInstrumentEstimate[];
  row: CtaInstrumentEstimate;
  onInstrumentChange: (instrument: string) => void;
  /** 形态六维均值：仅用于并排联动解释，绝不合入 CTA 读数 */
  regimeMean: number | null;
}) {
  const linkage = useMemo(() => {
    if (regimeMean === null || row.flow_score === null || row.position_score === null) return null;
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
          <h2 className="flex items-center gap-1.5 text-h3 text-ink-900">
            {t(row.label)}
            <span className="font-mono text-caption font-normal text-ink-400 tnum">{row.proxy_symbol}</span>
            <InfoHint hint={CTA_HINTS.overview} />
          </h2>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <span className="rounded-pill border border-line bg-paper-2 px-2 py-0.5 text-micro text-ink-500">
            {t('基于 ETF 趋势的代理估算')}
          </span>
          {rows.length > 1 && (
            <Segmented
              options={rows.map((item) => ({ value: item.instrument, label: t(item.label) }))}
              value={row.instrument}
              onChange={onInstrumentChange}
              className="[&_button]:text-micro"
            />
          )}
        </div>
      </div>

      {row.source_status !== 'active' || row.position_score === null ? (
        /* 诚实空态：历史不足/代理不可用时明示，不以中性值代替 */
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
          {/* 大仓位读数 + 模型分解 ｜ 仓位历史 */}
          <div className="mt-4 grid grid-cols-1 gap-6 lg:grid-cols-12">
            <div className="space-y-3 lg:col-span-4">
              <div className="rounded-md bg-paper-2 px-3 py-3">
                <p className="flex items-center gap-1 text-micro text-ink-400">
                  {t('估算目标仓位')}
                  <InfoHint hint={CTA_HINTS.position} size={10} />
                </p>
                <p className="mt-1 font-mono text-data-xl text-ink-900 tnum">
                  {signed(row.position_score)}
                  <span className="ml-2 text-micro font-normal text-ink-400">
                    {t('前值 {v}', { v: signed(row.previous_position_score) })}
                  </span>
                </p>
                <div className="mt-2"><PositionBar value={row.position_score} /></div>
                <p className="mt-1 flex justify-between font-mono text-micro text-ink-300 tnum" aria-hidden="true">
                  <span>-100</span><span>0</span><span>+100</span>
                </p>
              </div>

              <div className="flex flex-wrap items-center gap-1.5">
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

              <div className="grid grid-cols-2 gap-3">
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
                    {t('趋势读数')}
                    <InfoHint hint={CTA_HINTS.agreement} size={10} />
                  </p>
                  {/* 审计口径：大号 100% 视觉像「高置信度」，实际只表方向同向。
                      主读数改趋势强度（波动率缩放前，±100），方向/覆盖/缩放小字并列。 */}
                  <p className="mt-0.5 font-mono text-body font-semibold text-ink-900 tnum">
                    {t('强度 {v}', { v: signed(row.trend_strength) })}
                  </p>
                  <p className="font-mono text-micro text-ink-400 tnum">
                    {t('{d} 同向 · 覆盖 {c} · 缩放 ×{s}', {
                      d: directionCount(row),
                      c: row.active_model_weight !== null ? `${Math.round(row.active_model_weight * 100)}%` : '—',
                      s: row.volatility ? row.volatility.scalar.toFixed(2) : '—',
                    })}
                  </p>
                </div>
              </div>

              <div>
                <p className="flex items-center gap-1 text-micro text-ink-400">
                  {t('模型分解')}
                  <InfoHint hint={CTA_HINTS.submodels} size={10} />
                </p>
                <div className="mt-1.5 space-y-1.5">
                  {row.submodels && (['fast', 'medium', 'slow'] as const).map((key) => (
                    row.submodels![key] && (
                      <SignalRow key={key} label={t(row.submodels![key].label)} signal={row.submodels![key].signal} />
                    )
                  ))}
                </div>
              </div>

              {row.volatility && (
                <div className="rounded-md bg-paper-2 px-3 py-2">
                  <p className="text-micro text-ink-400">{t('波动率缩放')}</p>
                  <p className="mt-0.5 font-mono text-micro text-ink-600 tnum">
                    {t('已实现波动 {rv}% · 目标 {tv}% → 缩放 ×{s}', {
                      rv: row.volatility.realized_annual !== null ? (row.volatility.realized_annual * 100).toFixed(1) : '—',
                      tv: (row.volatility.target_annual * 100).toFixed(0),
                      s: row.volatility.scalar.toFixed(2),
                    })}
                  </p>
                </div>
              )}
            </div>

            <div className="lg:col-span-8">
              <p className="flex items-center gap-1 text-micro text-ink-400">
                {t('仓位历史')}
                <InfoHint hint={CTA_HINTS.position} size={10} />
              </p>
              <PositionHistoryChart history={row.history} />
              <p className="mt-1 text-micro text-ink-400">{t('每日收盘后的同口径估算读数')}</p>
            </div>
          </div>

          {/* 触发阶梯 ｜ 情景曲线 */}
          <div className="mt-6 grid grid-cols-1 gap-6 lg:grid-cols-12">
            <div className="lg:col-span-6">
              {/* 切标的重挂：触发区 id（above-1…）每只都复用，展开态不得跨标的迁移 */}
              <TriggerLadder key={row.instrument} row={row} />
            </div>
            <div className="lg:col-span-6">
              <p className="flex items-center gap-1 text-micro text-ink-400">
                {t('情景曲线：若明日收于横轴价格，估算目标仓位为纵轴值')}
                <InfoHint hint={CTA_HINTS.scenario} size={10} />
              </p>
              <ScenarioChart row={row} />
            </div>
          </div>

          {linkage && (
            <p className="mt-4 rounded-md bg-ai-50 px-3 py-2 text-caption text-ai-600">{linkage}</p>
          )}

          {row.warnings.length > 0 && (
            <div className="mt-4 rounded-md bg-warn-50 px-3 py-2">
              <p className="text-micro font-medium text-warn-700">{t('数据警告')}</p>
              <ul className="mt-1 list-inside list-disc space-y-0.5 text-micro text-warn-700">
                {row.warnings.map((w) => <li key={w}>{w}</li>)}
              </ul>
            </div>
          )}

          <p className="mt-4 text-micro text-ink-400">
            {t('数据截至 {d} 收盘', { d: row.data_through ?? '—' })}
            {/* 快照按墙钟变旧 ≠ 数据过期：休市期间只要覆盖最近已收盘交易日
                就是最新（GPT-5.6-Pro 审计问题 3 的双状态拆分）。 */}
            {row.market_data_current === true && <span> · {t('已是最新交易日')}</span>}
            {row.market_data_current === false && (
              <span className="text-warn-600"> · {t('晚于最近交易日，等待快照更新')}</span>
            )}
            {row.intraday?.provisional && <span> · {t('盘中读数为暂定，不入正式历史')}</span>}
            {' · '}{t('方法 {v} · 代理={p}', { v: data.method_version ?? '—', p: row.proxy_symbol })}
            {' · '}{t('代理模型估算，非任何机构真实仓位披露')}
          </p>
        </>
      )}
    </div>
  );
}
