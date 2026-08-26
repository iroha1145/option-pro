/** Segmented 分段切换：beui motion tabs 滑行指示器（spring 170/24/1.2） */
import { useId } from 'react';
import { MotionConfig, motion, useReducedMotion } from 'framer-motion';
import { cn } from '@/lib/utils';
import { SPRING_INDICATOR } from '@/lib/motion';
import GlidePill from '@/components/shared/GlidePill';

interface SegmentedProps<T extends string> {
  options: { value: T; label: string }[];
  value: T;
  onChange: (v: T) => void;
  className?: string;
}

export default function Segmented<T extends string>({ options, value, onChange, className }: SegmentedProps<T>) {
  const layoutId = useId();
  const reduce = useReducedMotion();

  return (
    <MotionConfig transition={reduce ? { duration: 0 } : SPRING_INDICATOR}>
      {/* layoutRoot：指示器的 layoutId 按页面坐标投影，固定/滚动容器里会把
          滚动偏移回放成位移；滑块只在条内移动，投影作用域收进 Tabs 包裹层。 */}
      <motion.div
        layoutRoot
        role="tablist"
        className={cn('t-tabs border border-line', className)}
      >
        {options.map((o, index) => {
          const active = value === o.value;
          return (
            <button
              key={o.value}
              role="tab"
              className="t-tab text-caption font-medium focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500/30"
              aria-selected={active}
              /* tablist 的标准键盘行为（审计 P3-5）：roving tabindex + 左右方向键 +
                 Home/End。旧实现只有 role，Tab 会逐个停在每一项，方向键完全无效。 */
              tabIndex={value === o.value ? 0 : -1}
              onClick={() => onChange(o.value)}
              onKeyDown={(event) => {
                const step =
                  event.key === 'ArrowRight' || event.key === 'ArrowDown'
                    ? 1
                    : event.key === 'ArrowLeft' || event.key === 'ArrowUp'
                      ? -1
                      : 0;
                let target = -1;
                if (step !== 0) {
                  target = (index + step + options.length) % options.length;
                } else if (event.key === 'Home') {
                  target = 0;
                } else if (event.key === 'End') {
                  target = options.length - 1;
                }
                if (target < 0) return;
                event.preventDefault();
                onChange(options[target].value);
                const next = event.currentTarget.parentElement?.querySelectorAll<HTMLElement>('.t-tab')[target];
                next?.focus();
              }}
            >
              {active && <GlidePill layoutId={layoutId} />}
              <span className="relative z-10">{o.label}</span>
            </button>
          );
        })}
      </motion.div>
    </MotionConfig>
  );
}
