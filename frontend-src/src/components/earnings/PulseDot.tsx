/**
 * PulseDot：led-pulse 心跳点（§4.2：scale 1→1.35→1 + 光晕 .6→0，1.5s ease-inout infinite）
 * 用于 AI 状态点 / 任务当前步。Framer Motion 实现以便自定义颜色（全站 led-pulse 为绿色专用）。
 */
import { motion } from 'framer-motion';
import { cn } from '@/lib/utils';

export default function PulseDot({ className, size = 8 }: { className?: string; size?: number }) {
  return (
    <span className="relative inline-flex shrink-0" style={{ width: size, height: size }} aria-hidden="true">
      <motion.span
        className={cn('absolute inset-0 rounded-full', className)}
        animate={{ scale: [1, 1.9], opacity: [0.55, 0] }}
        transition={{ duration: 1.5, repeat: Infinity, ease: [0.45, 0, 0.15, 1] }}
      />
      <motion.span
        className={cn('relative rounded-full', className)}
        style={{ width: size, height: size }}
        animate={{ scale: [1, 1.18, 1] }}
        transition={{ duration: 1.5, repeat: Infinity, ease: [0.45, 0, 0.15, 1] }}
      />
    </span>
  );
}
