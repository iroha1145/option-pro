/**
 * GlidePill 滑行指示器（beui.dev components/motion/tabs）：
 * active 项之间用共享 layoutId 做布局投影，弹簧物理来自 MotionConfig
 * （调用点包 SPRING_INDICATOR；reduced-motion 时 duration 归零、退回瞬切）。
 * 视觉沿用纸面分段控件的白底胶囊 + shadow-btn，与旧 t-tabs-pill 同皮。
 */
import { motion } from 'framer-motion';
import { cn } from '@/lib/utils';

export default function GlidePill({ layoutId, className }: { layoutId: string; className?: string }) {
  return (
    <motion.span
      layoutId={layoutId}
      layout="position"
      aria-hidden="true"
      className={cn('absolute inset-0 rounded-md bg-card shadow-btn', className)}
    />
  );
}
