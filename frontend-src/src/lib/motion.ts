/**
 * framer-motion 时长/缓动档位（v8.3）：与 design.md §4.1 / tailwind transitionDuration
 * 同一套档位，JS 侧散落的近邻字面量（0.48/0.5/0.7…）一律归并到这里。
 * 微交互 DUR_FAST · 小件 DUR_UI · 大区块/首屏入场 DUR_SECTION。
 */
export const EASE_PAPER = [0.16, 1, 0.3, 1] as const;
export const EASE_SNAP = [0.22, 1, 0.36, 1] as const;
export const DUR_FAST = 0.16;
export const DUR_UI = 0.24;
export const DUR_SECTION = 0.56;
