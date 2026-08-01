/**
 * InfoHint — 评分解释图标（圈 i）+ 悬停/聚焦/点按浮层
 *
 * 设计约束：
 * - 触发器是 span[role=button] 而非 <button>：评分常渲染在可点击行（motion.button）
 *   内部，真按钮会形成非法嵌套（TickerChip 同款教训）
 * - 点击/键盘触发要 stopPropagation，避免误触所在行的跳转
 * - 触屏无 hover，用受控 open 点按切换
 * - 文案来自 lib/scoreHints（源自后端真实算法），本组件不编内容
 *
 * 浮层挂在 document.body（portal + position:fixed），不留在触发点旁边：
 * 读数几乎都在 `card-surface overflow-hidden` 之类的裁剪盒里，同层的绝对定位
 * 浮层会被祖先的 overflow 直接切掉——z-index 对 overflow 裁剪无效，堆叠顺序
 * 再高也救不回来。列表首行的说明因此被削去半截（新闻流实拍）。挂到 body 之后
 * 裁剪盒管不着它，方向也能按视口剩余空间自动翻面。
 */
import {
  useCallback,
  useEffect,
  useId,
  useLayoutEffect,
  useRef,
  useState,
} from 'react';
import { createPortal } from 'react-dom';
import { cn } from '@/lib/utils';
import type { ScoreHint } from '@/lib/scoreHints';
import { t } from '../../i18n/core.ts';

/** 浮层最大宽度（px），与下方 maxWidth 内联样式保持同一常量。 */
const TOOLTIP_MAX_WIDTH = 300;
/** 浮层与视口边缘至少保留的间距（px）。 */
const VIEWPORT_GUTTER = 8;
/** 浮层与触发点之间的间隙（px）。 */
const TRIGGER_GAP = 6;
/**
 * 抽屉 70/71、命令面板 80/81、确认对话框 85/86 之上，Toast 90 之下：
 * 说明浮层要盖住它所在的层，但不该盖住系统级提示。
 */
const TOOLTIP_Z_INDEX = 88;

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
  const [hovered, setHovered] = useState(false);
  const [focused, setFocused] = useState(false);
  const rootRef = useRef<HTMLSpanElement | null>(null);
  const triggerRef = useRef<HTMLSpanElement | null>(null);
  const tooltipRef = useRef<HTMLSpanElement | null>(null);
  const tooltipId = useId();
  /* 浮层挂在 body，CSS 的 group-hover/focus-within 够不着它，显示条件收归 state。
     aria 与真实显示一一对应：键盘用户聚焦时读屏（与 Playwright 的可访问树查询）
     都必须拿得到它。 */
  const exposed = open || focused || hovered;
  const [coords, setCoords] = useState<{ left: number; top: number } | null>(null);

  /**
   * 按视口摆位：水平夹在视口内，垂直空间不够就翻面。
   *
   * 水平只用触发点算——浮层宽度是确定的 min(300, 视口宽 − 2×gutter)。垂直必须
   * 量浮层自身高度（文案长短差很多），所以先渲染再定位，未定位时保持透明，
   * 避免先闪到错位置。
   */
  const place = useCallback(() => {
    const node = triggerRef.current;
    const tip = tooltipRef.current;
    if (!node || !tip || typeof window === 'undefined') return;
    const trigger = node.getBoundingClientRect();
    // 处于 hidden 子树（如未展开的 Accordion）时 rect 全为 0，算出来会整体错位。
    if (trigger.width === 0 && trigger.height === 0) return;

    const viewportWidth = document.documentElement.clientWidth;
    const viewportHeight = document.documentElement.clientHeight;
    const width = Math.min(TOOLTIP_MAX_WIDTH, viewportWidth - VIEWPORT_GUTTER * 2);
    const preferred =
      align === 'start'
        ? trigger.left
        : align === 'end'
          ? trigger.right - width
          : trigger.left + trigger.width / 2 - width / 2;
    const rightmost = Math.max(VIEWPORT_GUTTER, viewportWidth - width - VIEWPORT_GUTTER);
    const left = Math.min(Math.max(preferred, VIEWPORT_GUTTER), rightmost);

    const height = tip.offsetHeight;
    const aboveTop = trigger.top - TRIGGER_GAP - height;
    const belowTop = trigger.bottom + TRIGGER_GAP;
    const fitsAbove = aboveTop >= VIEWPORT_GUTTER;
    const fitsBelow = belowTop + height <= viewportHeight - VIEWPORT_GUTTER;
    const placeAbove = side === 'top' ? fitsAbove || !fitsBelow : !fitsBelow && fitsAbove;
    // 两侧都塞不下（浮层比视口还高）时夹回视口，宁可盖住触发点也不出屏。
    const lowest = Math.max(VIEWPORT_GUTTER, viewportHeight - height - VIEWPORT_GUTTER);
    const top = Math.min(Math.max(placeAbove ? aboveTop : belowTop, VIEWPORT_GUTTER), lowest);

    setCoords({ left, top });
  }, [align, side]);

  /* 显示期间跟住触发点：页面滚动、窗口缩放、上方内容变高都会让它移动。 */
  useLayoutEffect(() => {
    if (!exposed) {
      setCoords(null);
      return;
    }
    place();
    window.addEventListener('resize', place);
    window.addEventListener('scroll', place, true);
    return () => {
      window.removeEventListener('resize', place);
      window.removeEventListener('scroll', place, true);
    };
  }, [exposed, place]);

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
      className={cn('relative inline-flex align-middle', className)}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => {
        setHovered(false);
        setOpen(false);
      }}
    >
      <span
        ref={triggerRef}
        role="button"
        tabIndex={0}
        aria-label={t('{title}：查看评分说明', { title: hint.title })}
        aria-expanded={open}
        aria-describedby={exposed ? tooltipId : undefined}
        onFocus={() => setFocused(true)}
        onBlur={() => setFocused(false)}
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

      {/* 收起时整个移出 DOM（审计 2.5.10）：常驻的 opacity-0 节点会被读屏当正文
          连读，每个 InfoHint 的几十字算法说明全部混进页面内容。 */}
      {exposed &&
        typeof document !== 'undefined' &&
        createPortal(
          <span
            ref={tooltipRef}
            id={tooltipId}
            role="tooltip"
            /* pointer-events-none：浮层已不是触发器的后代，可交互时鼠标移进去会
               先触发根节点的 mouseleave 而闪烁。说明是只读文本，不需要指针。 */
            className={cn(
              'pointer-events-none fixed w-max rounded-md border border-line bg-card px-3 py-2.5 text-left shadow-sh-3',
              'transition-opacity duration-fast',
              coords ? 'opacity-100' : 'opacity-0',
            )}
            style={{
              zIndex: TOOLTIP_Z_INDEX,
              left: coords?.left ?? 0,
              top: coords?.top ?? 0,
              maxWidth: `min(${TOOLTIP_MAX_WIDTH}px, calc(100vw - ${
                VIEWPORT_GUTTER * 2
              }px))`,
            }}
          >
            {/* scoreHints 的 title/body/note 在定义处已经 t() 过：再包一层会在
                EN/JA 下拿译文回查词典，DEV 的缺译告警被几十条假警报淹没。 */}
            <span className="block text-caption font-semibold text-ink-800">{hint.title}</span>
            <span className="mt-1 block whitespace-normal text-micro leading-relaxed text-ink-600">
              {hint.body}
            </span>
            {hint.note && (
              <span className="mt-1 block whitespace-normal text-micro leading-relaxed text-ink-400">
                {hint.note}
              </span>
            )}
          </span>,
          document.body,
        )}
    </span>
  );
}
