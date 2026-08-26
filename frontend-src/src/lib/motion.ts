/**
 * framer-motion 时长/缓动档位（v8.3）：与 design.md §4.1 / tailwind transitionDuration
 * 同一套档位，JS 侧散落的近邻字面量（0.48/0.5/0.7…）一律归并到这里。
 * 微交互 DUR_FAST · 小件 DUR_UI · 大区块/首屏入场 DUR_SECTION。
 */
import type { Transition } from 'framer-motion';

export const EASE_PAPER = [0.16, 1, 0.3, 1] as const;
export const EASE_SNAP = [0.22, 1, 0.36, 1] as const;
export const DUR_FAST = 0.16;
export const DUR_UI = 0.24;
export const DUR_SECTION = 0.56;

/**
 * 共享布局滑行弹簧（beui.dev components/motion/tabs 原值）。
 * 指示器类（tabs pill / 列表高亮）统一用它：一点过冲让滑块落定带活气，
 * 而不是生硬贴上去；reduced-motion 由调用点 MotionConfig 归零。
 */
export const SPRING_INDICATOR: Transition = {
  type: 'spring',
  stiffness: 170,
  damping: 24,
  mass: 1.2,
};
