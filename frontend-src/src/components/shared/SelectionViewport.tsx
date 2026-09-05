import type { ReactNode } from 'react';
import { motion } from 'framer-motion';
import { cn } from '@/lib/utils';

/** Mobile rails leave room above and below the selected item, even when scrolling. */
export default function SelectionViewport({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <motion.div layoutScroll className={cn('selection-viewport no-scrollbar', className)}>
      {children}
    </motion.div>
  );
}
