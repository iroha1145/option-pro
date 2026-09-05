import { usePrefersReducedMotion } from '@/hooks/usePrefersReducedMotion';
/**
 * GlidePill 滑行指示器（beui.dev components/motion/tabs）：
 * active 项之间用共享 layoutId 做布局投影，弹簧物理与 reduced-motion 归零
 * 都由本组件自持——调用点只放它，不必再包一层 MotionConfig（唯一会动的
 * 就是这个 span，把仪式散到每个调用点只会让第三个调用点抄错）。
 * 完整布局动画——位置与尺寸一起补间（不采用 position-only 投影：它只动
 * 位置、宽度瞬跳，短标签切长标签时「一边滑一边突然胖一圈」，审查 #113 阻断 4）。
 * 视觉采用浅品牌底、小圆角与细描边；选中状态不挤占主操作按钮的强调度。
 * 结构约定：与按钮同级放在各自的 relative wrapper 里（不塞进 button 内部），
 * 所有按钮 relative z-10 盖在滑块之上，滑行经过邻居时不遮文字。
 * data-glide-pill 是取证测试的稳定句柄：别用「无子元素的 aria-hidden span」
 * 这类结构指纹去找它（加一个装饰子元素就会静默失配）。
 */
import { motion } from 'framer-motion';
import { SPRING_INDICATOR } from '@/lib/motion';
import { cn } from '@/lib/utils';

export default function GlidePill({ layoutId, className }: { layoutId: string; className?: string }) {
  const reduce = usePrefersReducedMotion();
  return (
    <motion.span
      layoutId={layoutId}
      aria-hidden="true"
      data-glide-pill=""
      transition={reduce ? { duration: 0 } : SPRING_INDICATOR}
      className={cn('pointer-events-none absolute inset-0 rounded-sm bg-brand-50 ring-1 ring-inset ring-brand-100', className)}
    />
  );
}
