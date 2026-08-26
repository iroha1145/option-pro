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
      {/* layoutRoot：指示器的 layoutId 按页面坐标投影；MobileDock 是 fixed
          容器，投影作用域收进条内才不会把页面滚动偏移回放成位移。滑块只
          在条内移动，内联场景下收进 wrapper 同样正确。 */}
      <motion.div
        layoutRoot
        role="tablist"
        className={cn('t-tabs border border-line', className)}
      >
        {options.map((o, index) => {
          const active = value === o.value;
          return (
            /* 指示器与按钮同级（审查 #113 阻断 4）：滑块 z-auto 永远垫在
               z-10 的透明按钮之下，滑行经过邻居不遮文字。 */
            <div key={o.value} className="relative">
              {active && <GlidePill layoutId={layoutId} />}
              <button
                role="tab"
                className="t-tab relative z-10 text-caption font-medium focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500/30"
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
                  /* 从最近的 tablist 查全体标签：wrapper 结构下
                     parentElement 只剩当前标签的 wrapper（阻断 4 连锁修）。 */
                  const tabs = event.currentTarget
                    .closest<HTMLElement>('[role="tablist"]')
                    ?.querySelectorAll<HTMLElement>('.t-tab');
                  tabs?.[target]?.focus();
                }}
              >
                {o.label}
              </button>
            </div>
          );
        })}
      </motion.div>
    </MotionConfig>
  );
}
