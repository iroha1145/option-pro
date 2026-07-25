/** Segmented 分段切换：滑块 260ms ease-paper */
import { useLayoutEffect, useRef, useState } from 'react';
import { cn } from '@/lib/utils';

interface SegmentedProps<T extends string> {
  options: { value: T; label: string }[];
  value: T;
  onChange: (v: T) => void;
  className?: string;
}

export default function Segmented<T extends string>({ options, value, onChange, className }: SegmentedProps<T>) {
  const wrapRef = useRef<HTMLDivElement>(null);
  const [thumb, setThumb] = useState<{ left: number; width: number } | null>(null);

  useLayoutEffect(() => {
    const wrap = wrapRef.current;
    if (!wrap) return;
    const idx = options.findIndex((o) => o.value === value);
    const btn = wrap.children[idx + 1] as HTMLElement | undefined; // +1 跳过滑块
    if (btn) setThumb({ left: btn.offsetLeft, width: btn.offsetWidth });
  }, [value, options]);

  return (
    <div
      ref={wrapRef}
      role="tablist"
      className={cn('relative inline-flex items-center gap-0.5 rounded-md border border-line bg-card-warm p-0.5', className)}
    >
      <span
        aria-hidden="true"
        className="absolute top-0.5 bottom-0.5 rounded-[4px] bg-card shadow-sh-1 transition-[left,width,opacity] duration-ui ease-paper"
        style={thumb ? { left: thumb.left, width: thumb.width } : { opacity: 0 }}
      />
      {options.map((o) => (
        <button
          key={o.value}
          role="tab"
          aria-selected={value === o.value}
          onClick={() => onChange(o.value)}
          className={cn(
            'relative z-10 rounded-[4px] px-3 py-1 text-caption font-medium transition-colors duration-fast',
            value === o.value ? 'text-ink-800' : 'text-ink-400 hover:text-ink-600',
          )}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}
