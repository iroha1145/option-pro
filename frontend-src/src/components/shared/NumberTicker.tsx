import { memo } from 'react';
import { usePrefersReducedMotion } from '@/hooks/usePrefersReducedMotion';
import { cn } from '@/lib/utils';
import { numberGlyphs } from '@/lib/numberTicker';

const DIGITS = Array.from({ length: 10 }, (_, digit) => (
  <span key={digit} style={{ height: '1.1em', lineHeight: '1.1em' }}>{digit}</span>
));
const Digit = memo(function Digit({ char }: { char: string }) {
  return (
    <span className="relative inline-block overflow-hidden" style={{ height: '1.1em', width: '1ch' }}>
      <span className="absolute inset-x-0 top-0 flex flex-col items-center" style={{
        transform: `translateY(-${Number(char) * 1.1}em)`,
        transition: 'transform 250ms cubic-bezier(0.16, 1, 0.3, 1)',
      }}>{DIGITS}</span>
    </span>
  );
});

/** Stable place keys and CSS transforms; no per-digit JavaScript animator. */
const NumberTicker = memo(function NumberTicker({ text, className }: { text: string; className?: string }) {
  const reduce = usePrefersReducedMotion();
  return (
    <span className={cn('inline-flex items-center align-middle tabular-nums', className)} aria-label={text}>
      <span aria-hidden="true" className="inline-flex items-center">
        {reduce ? text : numberGlyphs(text).map(({ char, key }) => /\d/.test(char)
          ? <Digit key={key} char={char} />
          : <span key={key} style={{ lineHeight: '1.1em' }}>{char}</span>)}
      </span>
    </span>
  );
});
export default NumberTicker;
