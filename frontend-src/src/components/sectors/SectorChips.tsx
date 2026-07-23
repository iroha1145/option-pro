/**
 * 板块 chip 切换条（11 板块；选中态 spring-pop 放大 1.04，§4.1/§4.3）
 * 随 B1 热力砖选中联动，也可手动改（作用于 B3 IV 排名面板）
 */
import { motion } from 'framer-motion';
import { cn } from '@/lib/utils';

interface SectorChipsProps {
  sectors: { id: string; name: string }[];
  value: string;
  onChange: (id: string) => void;
  className?: string;
}

export default function SectorChips({ sectors, value, onChange, className }: SectorChipsProps) {
  return (
    <div
      role="tablist"
      aria-label="板块切换"
      className={cn('flex gap-1.5 overflow-x-auto py-0.5 no-scrollbar', className)}
    >
      {sectors.map((s) => {
        const active = s.id === value;
        return (
          <motion.button
            key={s.id}
            type="button"
            role="tab"
            aria-selected={active}
            animate={{ scale: active ? 1.04 : 1 }}
            whileTap={{ scale: 0.96 }}
            transition={{ type: 'spring', stiffness: 520, damping: 32 }}
            onClick={() => onChange(s.id)}
            className={cn(
              'shrink-0 rounded-pill border px-3 py-1.5 text-caption transition-colors duration-fast',
              active
                ? 'border-brand-600 bg-brand-100 font-semibold text-brand-700'
                : 'border-line bg-card text-ink-500 hover:border-line-strong hover:text-ink-800',
            )}
          >
            {s.name}
          </motion.button>
        );
      })}
    </div>
  );
}
