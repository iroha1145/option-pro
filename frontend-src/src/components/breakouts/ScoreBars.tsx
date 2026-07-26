/**
 * 评分套组条（§6-5 强度色阶 + grow-bar 700ms）
 * mini：信号卡 3×3 迷你格；full：详情面板整行条组。
 * 追高风险反向着色（riskBarClass）。
 * live 契约评分可空：值缺失显「—」+ 空轨道（留空优于编造）。
 */
import { motion } from 'framer-motion';
import { cn } from '@/lib/utils';
import { SCORE_DEFS, riskBarClass, scoreBarClass } from './types';
import type { BreakoutEventFull, RangePersistence } from './types';
import { t } from '../../i18n/core.ts';

function barCls(key: string, v: number): string {
  return key === 'chase_risk_score' ? riskBarClass(v) : scoreBarClass(v);
}

const fin = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v);
/** 显示值：数值取整展示，缺失「—」 */
const disp = (v: unknown): string => (fin(v) ? String(Math.round(v)) : '—');

export function ScoreBarsMini({ event, className }: { event: BreakoutEventFull; className?: string }) {
  return (
    <div className={cn('grid grid-cols-3 gap-x-3 gap-y-1.5', className)} aria-label={t("评分套组")}>
      {SCORE_DEFS.map((d, i) => {
        const v = event[d.key];
        return (
          <div key={d.key} title={`${d.label} ${disp(v)}`}>
            <p className="flex items-baseline justify-between">
              <span className="text-[10px] leading-[14px] text-ink-400">{d.label}</span>
              <span className="font-mono text-[10px] leading-[14px] text-ink-500 tnum">{disp(v)}</span>
            </p>
            <div className="mt-0.5 h-[3px] overflow-hidden rounded-pill bg-line">
              {fin(v) && (
                <motion.div
                  className={cn('h-full origin-left rounded-pill', barCls(d.key, v))}
                  initial={{ scaleX: 0 }}
                  whileInView={{ scaleX: 1 }}
                  viewport={{ once: true, amount: 0.4 }}
                  transition={{ duration: 0.7, ease: [0.16, 1, 0.3, 1], delay: i * 0.03 }}
                  style={{ width: `${Math.max(3, Math.min(100, v))}%` }}
                />
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}

export function ScoreBarsFull({ event, className }: { event: BreakoutEventFull; className?: string }) {
  return (
    <div className={cn('space-y-2', className)} aria-label={t("评分套组")}>
      {SCORE_DEFS.map((d, i) => {
        const v = event[d.key];
        return (
          <div key={d.key} className="grid grid-cols-[64px_1fr_30px] items-center gap-2.5">
            <span className="text-caption text-ink-500">{d.label}</span>
            <div className="h-[3px] overflow-hidden rounded-pill bg-line">
              {fin(v) && (
                <motion.div
                  className={cn('h-full origin-left rounded-pill', barCls(d.key, v))}
                  initial={{ scaleX: 0 }}
                  animate={{ scaleX: 1 }}
                  transition={{ duration: 0.7, ease: [0.16, 1, 0.3, 1], delay: 0.1 + i * 0.045 }}
                  style={{ width: `${Math.max(3, Math.min(100, v))}%` }}
                />
              )}
            </div>
            <span className="text-right font-mono text-caption text-ink-600 tnum">{disp(v)}</span>
          </div>
        );
      })}
    </div>
  );
}

/** 区间持续指标：生产显示真实标量/斜率/分位，mock 兼容早期五维。 */
import { RANGE_PERSISTENCE_DEFS } from './types';

export function RangePersistenceBars({ event, className }: { event: BreakoutEventFull; className?: string }) {
  const rp = event.range_persistence as BreakoutEventFull['range_persistence'] | null | undefined;
  if (rp && typeof rp === 'object' && 'kind' in rp && rp.kind === 'live') {
    const metrics = [
      { key: 'value', label: t('持续分'), value: rp.value },
      { key: 'ratio10d', label: t('10 日占比'), value: rp.ratio10d },
      { key: 'globalPercentile', label: t('全局分位'), value: rp.globalPercentile },
      { key: 'sectorPercentile', label: t('板块分位'), value: rp.sectorPercentile },
      { key: 'selfPercentile', label: t('自身分位'), value: rp.selfPercentile },
    ] as const;
    const statusCn: Record<string, string> = {
      active: t('有效'),
      warming_up: t('积累中'),
      insufficient_history: t('历史不足'),
      unavailable: t('暂不可用'),
    };
    return (
      <div className={cn('space-y-2', className)} aria-label={t("区间持续指标")}>
        <div className="mb-2 flex items-center justify-between rounded-sm bg-card-warm px-2.5 py-1.5">
          <span className="text-caption text-ink-500">{t('状态')}</span>
          <span className="font-mono text-caption text-ink-700">{statusCn[rp.status] ?? rp.status}</span>
        </div>
        {metrics.map((metric, i) => (
          <div key={metric.key} className="grid grid-cols-[64px_1fr_38px] items-center gap-2.5">
            <span className="text-caption text-ink-500">{metric.label}</span>
            <div className="h-[3px] overflow-hidden rounded-pill bg-line">
              {fin(metric.value) && (
                <motion.div
                  className={cn('h-full origin-left rounded-pill', scoreBarClass(metric.value))}
                  initial={{ scaleX: 0 }}
                  animate={{ scaleX: 1 }}
                  transition={{ duration: 0.7, ease: [0.16, 1, 0.3, 1], delay: 0.06 + i * 0.045 }}
                  style={{ width: `${Math.max(3, Math.min(100, metric.value))}%` }}
                />
              )}
            </div>
            <span className="text-right font-mono text-caption text-ink-600 tnum">{disp(metric.value)}</span>
          </div>
        ))}
        <p className="pt-1 text-right font-mono text-micro text-ink-400 tnum">
          {t('5 日斜率')} {fin(rp.slope5d) ? `${rp.slope5d >= 0 ? '+' : ''}${rp.slope5d.toFixed(2)}` : '—'}
        </p>
      </div>
    );
  }
  const legacy = rp as RangePersistence | null | undefined;
  const hasDims = legacy && RANGE_PERSISTENCE_DEFS.every((d) => fin(legacy[d.key]));
  if (!hasDims) {
    return (
      <div className={cn('space-y-2', className)} aria-label={t("区间持续指标")}>
        <p className="flex h-[104px] items-center justify-center rounded-md border border-line bg-card-warm text-caption text-ink-400">
          {t('区间持续指标暂不可用')}
        </p>
      </div>
    );
  }
  return (
    <div className={cn('space-y-2', className)} aria-label={t("区间持续五维")}>
      {RANGE_PERSISTENCE_DEFS.map((d, i) => {
        const v = legacy[d.key];
        return (
          <div key={d.key} className="grid grid-cols-[64px_1fr_30px] items-center gap-2.5">
            <span className="text-caption text-ink-500">{d.label}</span>
            <div className="h-[3px] overflow-hidden rounded-pill bg-line">
              <motion.div
                className={cn('h-full origin-left rounded-pill', scoreBarClass(v))}
                initial={{ scaleX: 0 }}
                animate={{ scaleX: 1 }}
                transition={{ duration: 0.7, ease: [0.16, 1, 0.3, 1], delay: 0.06 + i * 0.045 }}
                style={{ width: `${Math.max(3, Math.min(100, v))}%` }}
              />
            </div>
            <span className="text-right font-mono text-caption text-ink-600 tnum">{disp(v)}</span>
          </div>
        );
      })}
    </div>
  );
}
