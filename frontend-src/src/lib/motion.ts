/**
 * framer-motion 时长/缓动档位（v8.3）：与 design.md §4.1 / tailwind transitionDuration
 * 同一套档位，JS 侧散落的近邻字面量（0.48/0.5/0.7…）一律归并到这里。
 * 微交互 DUR_FAST · 小件 DUR_UI · 大区块/首屏入场 DUR_SECTION。
 */
import type { Transition } from 'framer-motion';

export const EASE_PAPER = [0.16, 1, 0.3, 1] as const;
export const DUR_FAST = 0.16;
export const DUR_UI = 0.24;
export const DUR_SECTION = 0.56;

/**
 * 共享布局滑行弹簧（beui.dev components/motion/tabs 的 layoutId 弹簧）。
 * 2026-09-18 上游从 170/24/1.2 改为 245/36/1.2：阻尼比约 0.84 → 1.05，不再过冲。
 * 理由是可横向滚动的标签条里，哪怕一点过冲也会把滚动条闪出来——本项目的
 * Segmented 正放在 HorizontalScroller 里。指示器类（tabs pill / 列表高亮）统一用它；
 * reduced-motion 归零由 GlidePill 自持（唯一会动的就是那个 span，不需要每个
 * 调用点再包一层 MotionConfig）。
 */
export const SPRING_INDICATOR: Transition = {
  type: 'spring',
  stiffness: 245,
  damping: 36,
  mass: 1.2,
};

/**
 * 小件弹出弹簧（浮层、徽标、行内卡片的出现）：高刚度、几乎不过冲，读作「利落
 * 地到位」。此前 520/32 在七处各写一份（两处局部常量、五处内联），收口到这里。
 * 与 beUI 的 SPRING_PRESS（500/30）同档；数值与价格类元素仍不用弹簧。
 */
export const SPRING_POP = { type: 'spring', stiffness: 520, damping: 32 } as const;

/**
 * 数据条入场：变体挂在条上，initial="hidden" + whileInView="shown" 挂在轨道或
 * 外层容器上。条从 scale 0 起步时包围盒面积为 0，直接让它自己观察视口，
 * 有一部分永远判不进去，条一直空着，读起来像「没有数据」。
 */
export const GROW_X = { hidden: { scaleX: 0 }, shown: { scaleX: 1 } } as const;
export const GROW_Y = { hidden: { scaleY: 0 }, shown: { scaleY: 1 } } as const;
