/**
 * Segmented 分段切换：beui motion tabs 滑行指示器。
 * 唯一一份 tablist 键盘/结构实现——带徽标、可横向滚动的变体（screener 分档）
 * 也走这里，靠 renderLabel + scrollable 定制，不再各抄一份 onKeyDown。
 * 弹簧与 reduced-motion 归零由 GlidePill 自持，这里不包 MotionConfig。
 */
import { useId, type ReactNode } from 'react';
import { motion } from 'framer-motion';
import { cn } from '@/lib/utils';
import GlidePill from '@/components/shared/GlidePill';
import SelectionViewport from '@/components/shared/SelectionViewport';

interface SegmentedOption<T extends string> {
  value: T;
  label: string;
}

interface SegmentedProps<T extends string> {
  options: SegmentedOption<T>[];
  value: T;
  onChange: (v: T) => void;
  className?: string;
  /** 标签自定义渲染（如分档的 Mono 数量徽标）；缺省只渲染 label 文本。 */
  renderLabel?: (option: SegmentedOption<T>, active: boolean) => ReactNode;
  /** 本条可横向滚动：换 layoutScroll 投影，并挂上滚动容器与不收缩的项。 */
  scrollable?: boolean;
  ariaLabel?: string;
  title?: string;
}

export default function Segmented<T extends string>({
  options,
  value,
  onChange,
  className,
  renderLabel,
  scrollable = false,
  ariaLabel,
  title,
}: SegmentedProps<T>) {
  const layoutId = useId();

  return (
    /* 投影作用域二选一：layoutRoot 把 layoutId 的坐标收进条内（MobileDock 是
       fixed 容器，否则页面滚动偏移会被回放成位移）；可横向滚动的条必须改用
       layoutScroll，布局测量计入容器滚动偏移，否则滚动后切换滑块跳位。 */
    <SelectionViewport>
    <motion.div
      layoutRoot={!scrollable}
      layoutScroll={scrollable}
      role="tablist"
      aria-label={ariaLabel}
      title={title}
      className={cn('t-tabs selection-group border border-line', scrollable && 'no-scrollbar max-w-full overflow-x-auto', className)}
    >
      {options.map((o, index) => {
        const active = value === o.value;
        return (
          /* 指示器与按钮同级（审查 #113 阻断 4）：滑块 z-auto 永远垫在
             z-10 的透明按钮之下，滑行经过邻居不遮文字。 */
          <div key={o.value} className={cn('relative', scrollable && 'shrink-0')}>
            {active && <GlidePill layoutId={layoutId} />}
            <button
              type="button"
              role="tab"
              className={cn(
                't-tab relative z-10 text-caption font-medium focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500/30',
                scrollable && 'shrink-0 whitespace-nowrap',
              )}
              aria-selected={active}
              /* tablist 的标准键盘行为（审计 P3-5）：roving tabindex + 左右方向键 +
                 Home/End。旧实现只有 role，Tab 会逐个停在每一项，方向键完全无效。 */
              tabIndex={active ? 0 : -1}
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
              {renderLabel ? renderLabel(o, active) : o.label}
            </button>
          </div>
        );
      })}
    </motion.div>
    </SelectionViewport>
  );
}
