/** Segmented 分段切换：transitions.dev tabs sliding（pill 250ms） */
import { useLayoutEffect, useRef } from 'react';
import { cn } from '@/lib/utils';
import { placeTabsPill } from '@/lib/transitions';

interface SegmentedProps<T extends string> {
  options: { value: T; label: string }[];
  value: T;
  onChange: (v: T) => void;
  className?: string;
}

export default function Segmented<T extends string>({ options, value, onChange, className }: SegmentedProps<T>) {
  const wrapRef = useRef<HTMLDivElement>(null);
  const pillRef = useRef<HTMLSpanElement>(null);
  const firstPaint = useRef(true);

  useLayoutEffect(() => {
    const wrap = wrapRef.current;
    const pill = pillRef.current;
    if (!wrap || !pill) return;
    const idx = options.findIndex((o) => o.value === value);
    const btn = wrap.querySelectorAll<HTMLElement>('.t-tab')[idx];
    if (!btn) return;
    const animate = !firstPaint.current;
    firstPaint.current = false;
    placeTabsPill(pill, btn, animate);
  }, [value, options]);

  useLayoutEffect(() => {
    const onResize = () => {
      const wrap = wrapRef.current;
      const pill = pillRef.current;
      if (!wrap || !pill) return;
      const active = wrap.querySelector<HTMLElement>('.t-tab[aria-selected="true"]');
      if (active) placeTabsPill(pill, active, false);
    };
    window.addEventListener('resize', onResize);
    return () => window.removeEventListener('resize', onResize);
  }, []);

  return (
    <div
      ref={wrapRef}
      role="tablist"
      className={cn('t-tabs border border-line', className)}
    >
      <span ref={pillRef} className="t-tabs-pill" aria-hidden="true" />
      {options.map((o, index) => (
        <button
          key={o.value}
          role="tab"
          className="t-tab text-caption font-medium"
          aria-selected={value === o.value}
          /* tablist 的标准键盘行为（审计 P3-5）：roving tabindex + 左右方向键 +
             Home/End。旧实现只有 role，Tab 会逐个停在每一项，方向键完全无效。 */
          tabIndex={value === o.value ? 0 : -1}
          onClick={() => onChange(o.value)}
          onKeyDown={(event) => {
            const step =
              event.key === 'ArrowRight' || event.key === 'ArrowDown'
                ? 1
                : event.key === 'ArrowLeft' || event.key === 'ArrowUp'
                  ? -1
                  : 0;
            let target = -1;
            if (step !== 0) {
              target = (index + step + options.length) % options.length;
            } else if (event.key === 'Home') {
              target = 0;
            } else if (event.key === 'End') {
              target = options.length - 1;
            }
            if (target < 0) return;
            event.preventDefault();
            onChange(options[target].value);
            const next = wrapRef.current?.querySelectorAll<HTMLElement>('.t-tab')[target];
            next?.focus();
          }}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}
