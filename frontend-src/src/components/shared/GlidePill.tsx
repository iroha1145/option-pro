/**
 * GlidePill 滑行指示器（beui.dev components/motion/tabs）：
 * active 项之间用共享 layoutId 做布局投影，弹簧物理来自 MotionConfig
 * （调用点包 SPRING_INDICATOR；reduced-motion 时 duration 归零、退回瞬切）。
 * 完整布局动画——位置与尺寸一起补间（不采用 position-only 投影：它只动
 * 位置、宽度瞬跳，短标签切长标签时「一边滑一边突然胖一圈」，审查 #113 阻断 4）。
 * 视觉沿用纸面分段控件的白底胶囊 + shadow-btn，与旧 t-tabs-pill 同皮。
 * 结构约定：与按钮同级放在各自的 relative wrapper 里（不塞进 button 内部），
 * 所有按钮 relative z-10 盖在滑块之上，滑行经过邻居时不遮文字。
 */
import { motion } from 'framer-motion';
import { cn } from '@/lib/utils';

export default function GlidePill({ layoutId, className }: { layoutId: string; className?: string }) {
  return (
    <motion.span
      layoutId={layoutId}
      aria-hidden="true"
      className={cn('pointer-events-none absolute inset-0 rounded-md bg-card shadow-btn', className)}
    />
  );
}
