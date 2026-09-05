import { motion } from 'framer-motion';
import { usePrefersReducedMotion } from '@/hooks/usePrefersReducedMotion';
import { cn } from '@/lib/utils';

import { numberGlyphs } from '@/lib/numberTicker';

/** Decimal-aware adaptation of Cloud Monitor's per-digit columns. */
export default function NumberTicker({ text, className }: { text: string; className?: string }) {
  const reduce = usePrefersReducedMotion();
  return (
    <span className={cn('inline-flex items-center align-middle tabular-nums', className)} aria-label={text}>
      <span aria-hidden="true" className="inline-flex items-center">
        {numberGlyphs(text).map(({ char, key }) => /\d/.test(char) ? (
          <span key={key} className="relative inline-block overflow-hidden" style={{ height: '1.1em', width: '1ch' }}>
            <motion.span initial={false} animate={{ y: `-${Number(char) * 1.1}em` }} transition={{ duration: reduce ? 0 : 0.25, ease: [0.16, 1, 0.3, 1] }} className="absolute inset-x-0 top-0 flex flex-col items-center">
              {Array.from({ length: 10 }, (_, digit) => <span key={digit} style={{ height: '1.1em', lineHeight: '1.1em' }}>{digit}</span>)}
            </motion.span>
          </span>
        ) : <span key={key} style={{ lineHeight: '1.1em' }}>{char}</span>)}
      </span>
    </span>
  );
}
