/**
 * 价格标尺（breakouts.md B3/B4 核心组件）
 * 轨道：失效价 ──●── 目标价，三段渐变 down-600 → ink-300 → up-600
 * markers：shield 失效 / flag 触发 / target 目标；现价菱形游标 brand-600 + 2px 白描边
 * 首绘游标自触发位滑至现价位 700ms ease-paper；轮询变化 400ms 过渡 + tick-flash
 */
import { useEffect, useRef } from 'react';
import { motion } from 'framer-motion';
import { cn } from '@/lib/utils';
import { fmtPrice } from '@/lib/format';
import Icon from '@/components/icons';
import type { IconName } from '@/components/icons';

interface PriceScaleProps {
  /** live 契约可空（如无目标价 target_price）：缺失的标记不画、不编造 */
  invalidation: number | null | undefined;
  trigger: number | null | undefined;
  target: number | null | undefined;
  current: number | null | undefined;
  /** B4 放大版：四点完整价格标注 */
  large?: boolean;
  flash?: 'up' | 'down' | null;
  className?: string;
}

interface MarkerDef {
  key: string;
  label: string;
  icon: IconName;
  value: number;
  iconCls: string;
}

const fin = (v: number | null | undefined): v is number => typeof v === 'number' && Number.isFinite(v);
const edgeAnchor = (pct: number): string =>
  pct < 12 ? 'translate-x-0 text-left' : pct > 88 ? '-translate-x-full text-right' : '-translate-x-1/2 text-center';

export default function PriceScale({ invalidation, trigger, target, current, large = false, flash = null, className }: PriceScaleProps) {
  /* 首绘 700ms / 轮询 400ms（hooks 必须在任何 early return 之前） */
  const mounted = useRef(false);
  useEffect(() => {
    mounted.current = true;
  }, []);

  const known = [invalidation, trigger, target, current].filter(fin);
  /* 已知价位不足两个：画不出标尺，诚实空态 */
  if (known.length < 2 || !fin(current)) {
    return (
      <div className={cn('w-full', className)} aria-label="价格标尺数据不足">
        <div className="flex h-9 items-center justify-center rounded-md border border-line bg-card-warm font-mono text-micro text-ink-300">
          — 价位数据不足
        </div>
      </div>
    );
  }
  const lo = Math.min(...known);
  const hi = Math.max(...known);
  const span = Math.max(0.0001, hi - lo);
  const pad = span * 0.06;
  const min = lo - pad;
  const max = hi + pad;
  const x = (v: number) => Math.min(100, Math.max(0, ((v - min) / (max - min)) * 100));
  const cursorFrom = fin(trigger) ? trigger : current;

  const markers: MarkerDef[] = [
    { key: 'invalid', label: '失效', icon: 'shield' as const, value: invalidation as number, iconCls: 'text-down-600' },
    { key: 'trigger', label: '触发', icon: 'flag' as const, value: trigger as number, iconCls: 'text-brand-600' },
    { key: 'target', label: '目标', icon: 'target' as const, value: target as number, iconCls: 'text-up-600' },
  ].filter((m) => fin(m.value));

  return (
    <div
      className={cn('w-full', className)}
      aria-label={`价格标尺：失效 ${fin(invalidation) ? fmtPrice(invalidation) : '—'}，触发 ${fin(trigger) ? fmtPrice(trigger) : '—'}，目标 ${fin(target) ? fmtPrice(target) : '—'}，现价 ${fmtPrice(current)}`}
    >
      {/* marker 图标行 */}
      <div className="relative h-4">
        {markers.map((m) => (
          <span key={m.key} className={cn('absolute', edgeAnchor(x(m.value)))} style={{ left: `${x(m.value)}%` }}>
            <Icon name={m.icon} size={large ? 15 : 12} className={m.iconCls} />
          </span>
        ))}
      </div>
      {/* 轨道 + 游标 */}
      <div className="relative mt-1 h-1 rounded-pill" style={{ background: 'linear-gradient(90deg, var(--down-600), var(--ink-300) 50%, var(--up-600))', opacity: 0.85 }}>
        {markers.map((m) => (
          <span
            key={m.key}
            className="absolute top-1/2 size-[3px] -translate-x-1/2 -translate-y-1/2 rounded-full bg-ink-500"
            style={{ left: `${x(m.value)}%` }}
            aria-hidden="true"
          />
        ))}
        <motion.span
          initial={{ left: `${x(cursorFrom)}%` }}
          animate={{ left: `${x(current)}%` }}
          transition={{ duration: mounted.current ? 0.4 : 0.7, ease: [0.16, 1, 0.3, 1] }}
          className="absolute top-1/2 -translate-x-1/2 -translate-y-1/2"
        >
          <span className="block size-2.5 rotate-45 rounded-[2px] bg-brand-600 shadow-[0_0_0_2px_#fff]" aria-hidden="true" />
        </motion.span>
      </div>
      {/* 标注行 */}
      {large ? (
        <div className="relative mt-2 h-8">
          {markers.map((m) => (
            <span key={m.key} className={cn('absolute', edgeAnchor(x(m.value)))} style={{ left: `${x(m.value)}%` }}>
              <span className="block text-[10px] leading-[14px] text-ink-400">{m.label}</span>
              <span className="block font-mono text-micro leading-[14px] text-ink-600 tnum">{fmtPrice(m.value)}</span>
            </span>
          ))}
          <motion.span
            initial={{ left: `${x(cursorFrom)}%` }}
            animate={{ left: `${x(current)}%` }}
            transition={{ duration: mounted.current ? 0.4 : 0.7, ease: [0.16, 1, 0.3, 1] }}
            className={cn('absolute', edgeAnchor(x(current)))}
          >
            <span className="block text-[10px] leading-[14px] text-brand-600">现价</span>
            <span
              className={cn(
                'block rounded-xs px-0.5 font-mono text-micro font-semibold leading-[14px] text-brand-700 tnum',
                flash === 'up' && 'animate-tick-flash-up',
                flash === 'down' && 'animate-tick-flash-down',
              )}
            >
              {fmtPrice(current)}
            </span>
          </motion.span>
        </div>
      ) : (
        <div className="relative mt-1.5 h-4">
          <motion.span
            initial={{ left: `${x(cursorFrom)}%` }}
            animate={{ left: `${x(current)}%` }}
            transition={{ duration: mounted.current ? 0.4 : 0.7, ease: [0.16, 1, 0.3, 1] }}
            className={cn('absolute', edgeAnchor(x(current)))}
          >
            <span
              className={cn(
                'rounded-xs px-0.5 font-mono text-micro font-semibold leading-[16px] text-ink-800 tnum',
                flash === 'up' && 'animate-tick-flash-up',
                flash === 'down' && 'animate-tick-flash-down',
              )}
            >
              {fmtPrice(current)}
            </span>
          </motion.span>
        </div>
      )}
    </div>
  );
}
