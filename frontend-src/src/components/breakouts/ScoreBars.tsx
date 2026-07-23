/**
 * 评分套组条（§6-5 强度色阶 + grow-bar 700ms）
 * mini：信号卡 3×3 迷你格；full：详情面板整行条组。
 * 追高风险反向着色（riskBarClass）。
 * live 契约评分可空：值缺失显「—」+ 空轨道（留空优于编造）。
 */
import { motion } from 'framer-motion';
import { cn } from '@/lib/utils';
import { SCORE_DEFS, riskBarClass, scoreBarClass } from './types';
import type { BreakoutEventFull } from './types';

function barCls(key: string, v: number): string {
  return key === 'chase_risk_score' ? riskBarClass(v) : scoreBarClass(v);
}

const fin = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v);
/** 显示值：数值取整展示，缺失「—」 */
const disp = (v: unknown): string => (fin(v) ? String(Math.round(v)) : '—');

export function ScoreBarsMini({ event, className }: { event: BreakoutEventFull; className?: string }) {
  return (
    <div className={cn('grid grid-cols-3 gap-x-3 gap-y-1.5', className)} aria-label="评分套组">
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
    <div className={cn('space-y-2', className)} aria-label="评分套组">
      {SCORE_DEFS.map((d, i) => {
        const v = event[d.key];
        return (
          <div key={d.key} className="grid grid-cols-[64px_1fr_30px] items-center gap-2.5">
            <span className="text-caption text-ink-500">{d.label}</span>
            <div className="h-1 overflow-hidden rounded-pill bg-line">
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

/** range_persistence 五维条（详情面板）；live 契约为单值（无五维）→ 诚实空态 */
import { RANGE_PERSISTENCE_DEFS } from './types';

export function RangePersistenceBars({ event, className }: { event: BreakoutEventFull; className?: string }) {
  const rp = event.range_persistence as BreakoutEventFull['range_persistence'] | null | undefined;
  const hasDims = rp && typeof rp === 'object' && RANGE_PERSISTENCE_DEFS.every((d) => fin(rp[d.key]));
  if (!hasDims) {
    return (
      <div className={cn('space-y-2', className)} aria-label="区间持续五维">
        <p className="flex h-[104px] items-center justify-center rounded-md border border-line bg-card-warm text-caption text-ink-400">
          接口未覆盖五维数据 · 留空而非编造
        </p>
      </div>
    );
  }
  return (
    <div className={cn('space-y-2', className)} aria-label="区间持续五维">
      {RANGE_PERSISTENCE_DEFS.map((d, i) => {
        const v = rp[d.key];
        return (
          <div key={d.key} className="grid grid-cols-[64px_1fr_30px] items-center gap-2.5">
            <span className="text-caption text-ink-500">{d.label}</span>
            <div className="h-1 overflow-hidden rounded-pill bg-line">
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
