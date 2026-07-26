/**
 * InfoHint — 评分解释图标（圈 i）+ 悬停/聚焦/点按浮层
 *
 * 设计约束：
 * - 触发器是 span[role=button] 而非 <button>：评分常渲染在可点击行（motion.button）
 *   内部，真按钮会形成非法嵌套（TickerChip 同款教训）
 * - 点击/键盘触发要 stopPropagation，避免误触所在行的跳转
 * - 悬停/focus-within 用 CSS 显示；触屏无 hover，用受控 open 点按切换
 * - 文案来自 lib/scoreHints（源自后端真实算法），本组件不编内容
 */
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { cn } from '@/lib/utils';
import type { ScoreHint } from '@/lib/scoreHints';
import { t } from '../../i18n/core.ts';

/** 浮层最大宽度（px），与下方 maxWidth 内联样式保持同一常量。 */
const TOOLTIP_MAX_WIDTH = 300;
/** 浮层与视口左右边缘至少保留的间距（px）。 */
const VIEWPORT_GUTTER = 8;

function InfoGlyph({ size = 13 }: { size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 16 16"
      fill="none"
      aria-hidden="true"
      className="shrink-0"
    >
      <circle cx="8" cy="8" r="6.6" stroke="currentColor" strokeWidth="1.3" />
      <circle cx="8" cy="5.1" r="0.9" fill="currentColor" />
      <path d="M8 7.4v3.8" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
    </svg>
  );
}

export default function InfoHint({
  hint,
  side = 'top',
  align = 'center',
  size = 13,
  className,
}: {
  hint: ScoreHint;
  /** 浮层出现的方向；表格首行/页脚等边缘位置按需选 bottom */
  side?: 'top' | 'bottom';
  /** 水平对齐；靠近容器右缘时用 end，左缘时用 start */
  align?: 'start' | 'center' | 'end';
  size?: number;
  className?: string;
}) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLSpanElement | null>(null);
  const triggerRef = useRef<HTMLSpanElement | null>(null);
  const [offsetLeft, setOffsetLeft] = useState<number | null>(null);

  /**
   * 水平位置按视口收敛。
   *
   * 浮层是绝对定位、锚定在触发点上，窄屏（320px）时 start/center/end 三种对齐
   * 都可能越过视口边缘：即使处于 opacity-0 也仍占布局盒，会把文档撑出横向滚动。
   * 这里只用「触发点位置」计算——浮层宽度是确定的 min(300, 视口宽 − 2×gutter)，
   * 不需要测量浮层本身，因此不会出现先错位再纠正的闪烁。
   * align 仍是首选对齐；空间足够时结果与原来逐像素一致，宽屏观感不变。
   */
  const measure = useCallback(() => {
    const node = triggerRef.current;
    const root = rootRef.current;
    if (!node || !root || typeof window === 'undefined') return;
    const trigger = node.getBoundingClientRect();
    const origin = root.getBoundingClientRect();
    // 处于 hidden 子树（如未展开的 Accordion）时 rect 全为 0，此时算出的偏移量
    // 在真正显示后会整体错位。保持未测量状态，交给类名兜底，等显示时再量。
    if (trigger.width === 0 && trigger.height === 0) return;
    const viewport = document.documentElement.clientWidth;
    const width = Math.min(TOOLTIP_MAX_WIDTH, viewport - VIEWPORT_GUTTER * 2);
    const preferred =
      align === 'start'
        ? trigger.left
        : align === 'end'
          ? trigger.right - width
          : trigger.left + trigger.width / 2 - width / 2;
    const highest = Math.max(VIEWPORT_GUTTER, viewport - width - VIEWPORT_GUTTER);
    const clamped = Math.min(Math.max(preferred, VIEWPORT_GUTTER), highest);
    setOffsetLeft(clamped - origin.left);
  }, [align]);

  useLayoutEffect(() => {
    measure();
    window.addEventListener('resize', measure);
    return () => window.removeEventListener('resize', measure);
  }, [measure]);

  /**
   * 再在「即将显示」时量一次。
   *
   * 存下来的是相对触发点的偏移量，而页面后续的重排（图表渲染完、上方内容变高、
   * 字体加载）会让触发点自己移动，只在挂载时量会漂移。悬停/聚焦时重量一次，
   * 保证读者真正看到浮层的那一刻位置是准的。
   */
  const remeasure = useCallback(() => measure(), [measure]);

  /* 触屏点按打开后，点页面其他位置应关闭 */
  useEffect(() => {
    if (!open) return;
    const onDocPointer = (event: PointerEvent) => {
      if (rootRef.current && !rootRef.current.contains(event.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener('pointerdown', onDocPointer);
    return () => document.removeEventListener('pointerdown', onDocPointer);
  }, [open]);

  return (
    <span
      ref={rootRef}
      className={cn('group/hint relative inline-flex align-middle', className)}
      onMouseEnter={remeasure}
      onFocusCapture={remeasure}
      onMouseLeave={() => setOpen(false)}
    >
      <span
        ref={triggerRef}
        role="button"
        tabIndex={0}
        aria-label={t('{title}：查看评分说明', { title: t(hint.title) })}
        className={cn(
          'inline-flex cursor-help items-center rounded-full text-ink-300 outline-none transition-colors duration-fast',
          'hover:text-brand-600 focus-visible:text-brand-600',
          open && 'text-brand-600',
        )}
        onClick={(event) => {
          event.stopPropagation();
          event.preventDefault();
          setOpen((value) => !value);
        }}
        onKeyDown={(event) => {
          if (event.key === 'Enter' || event.key === ' ') {
            event.stopPropagation();
            event.preventDefault();
            setOpen((value) => !value);
          } else if (event.key === 'Escape') {
            setOpen(false);
          }
        }}
      >
        <InfoGlyph size={size} />
      </span>

      <span
        role="tooltip"
        className={cn(
          'pointer-events-none absolute z-50 w-max rounded-md border border-line bg-card px-3 py-2.5 text-left shadow-sh-3',
          'opacity-0 transition-opacity duration-fast',
          'group-hover/hint:pointer-events-auto group-hover/hint:opacity-100',
          'group-focus-within/hint:pointer-events-auto group-focus-within/hint:opacity-100',
          open && 'pointer-events-auto opacity-100',
          side === 'top' ? 'bottom-full mb-1.5' : 'top-full mt-1.5',
          // 首帧（尚未测量）用类名按 align 摆位，避免闪到左上角。
          offsetLeft === null && align === 'center' && 'left-1/2 -translate-x-1/2',
          offsetLeft === null && align === 'start' && 'left-0',
          offsetLeft === null && align === 'end' && 'right-0',
        )}
        style={
          offsetLeft === null
            ? { maxWidth: TOOLTIP_MAX_WIDTH }
            : {
                left: offsetLeft,
                right: 'auto',
                transform: 'none',
                maxWidth: `min(${TOOLTIP_MAX_WIDTH}px, calc(100vw - ${
                  VIEWPORT_GUTTER * 2
                }px))`,
              }
        }
      >
        <span className="block text-caption font-semibold text-ink-800">{t(hint.title)}</span>
        <span className="mt-1 block whitespace-normal text-micro leading-relaxed text-ink-600">
          {t(hint.body)}
        </span>
        {hint.note && (
          <span className="mt-1 block whitespace-normal text-micro leading-relaxed text-ink-400">
            {t(hint.note)}
          </span>
        )}
      </span>
    </span>
  );
}
