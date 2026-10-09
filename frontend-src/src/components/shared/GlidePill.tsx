import { usePrefersReducedMotion } from '@/hooks/usePrefersReducedMotion';
/**
 * GlidePill 滑行指示器（beui.dev components/motion/tabs）：
 * active 项之间用共享 layoutId 做布局投影，弹簧物理与 reduced-motion 归零
 * 都由本组件自持——调用点只放它，不必再包一层 MotionConfig（唯一会动的
 * 就是这个 span，把仪式散到每个调用点只会让第三个调用点抄错）。
 * 完整布局动画——位置与尺寸一起补间（不采用 position-only 投影：它只动
 * 位置、宽度瞬跳，短标签切长标签时「一边滑一边突然胖一圈」，审查 #113 阻断 4）。
 * 视觉采用白底、柔和阴影的小圆角浮片；颜色和边界由共享控件样式统一。
 * 结构约定：与按钮同级放在各自的 relative wrapper 里（不塞进 button 内部），
 * 所有按钮 relative z-10 盖在滑块之上，滑行经过邻居时不遮文字。
 * data-glide-pill 是取证测试的稳定句柄：别用「无子元素的 aria-hidden span」
 * 这类结构指纹去找它（加一个装饰子元素就会静默失配）。
 */
import { useState } from 'react';
import { motion } from 'framer-motion';
import { SPRING_INDICATOR } from '@/lib/motion';
import { cn } from '@/lib/utils';

/* 2026-10-09：打开新页面时滑块先出现在附近、再滑到正确位置（用户反馈）。两个来源：
   1. 调用点用 useId 当 layoutId，它只由组件在树里的位置决定——换页后新页面同位置的控件拿到
      同一个标识，动画库把它当成同一个滑块，从旧页面的位置滑过来。调用点改用 useGlideLayoutId，
      每个实例一个新标识。
   2. 页面入场的位移动画期间，任何一次重绘都会被量成「位置变了」而补一段动画。传 dependency
      （选中值）后，只有选中项真的变了才做布局动画（Arc：动效只解释因果）。 */
let glideSerial = 0;
export function useGlideLayoutId(prefix = 'glide'): string {
  const [id] = useState(() => `${prefix}-${++glideSerial}`);
  return id;
}

export default function GlidePill({ layoutId, className, dependency }: { layoutId: string; className?: string; dependency?: unknown }) {
  const reduce = usePrefersReducedMotion();
  return (
    <motion.span
      layoutId={layoutId}
      layoutDependency={dependency}
      aria-hidden="true"
      data-glide-pill=""
      transition={reduce ? { duration: 0 } : SPRING_INDICATOR}
      className={cn('selection-indicator pointer-events-none absolute inset-0', className)}
    />
  );
}
