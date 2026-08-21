/**
 * 自定义下拉：catalog t-dropdown，不用系统菜单。
 * 选股 Top N / 成交额下限、个股期权到期日共用。
 */
import { useEffect, useRef, useState, type ReactNode } from 'react';
import { cn } from '@/lib/utils';
import {
  overlayClassName,
  overlayVisible,
  readRootDurationMs,
  useOverlayPhase,
} from '@/lib/transitions';
import Icon from '@/components/icons';

export default function MenuSelect<T extends string | number>({
  value,
  onChange,
  options,
  ariaLabel,
  className,
  triggerClassName,
  align = 'left',
  leading,
}: {
  value: T;
  onChange: (v: T) => void;
  options: { value: T; label: string }[];
  ariaLabel?: string;
  className?: string;
  triggerClassName?: string;
  align?: 'left' | 'right';
  leading?: ReactNode;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const closeMs = readRootDurationMs('--dropdown-close-dur', 150);
  const phase = useOverlayPhase(open, closeMs);
  const mounted = overlayVisible(open, phase);
  const current = options.find((o) => o.value === value) ?? options[0];

  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (!ref.current?.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false);
    };
    document.addEventListener('mousedown', onDoc);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDoc);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);

  return (
    <div ref={ref} className={cn('relative', className)}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-label={ariaLabel}
        className={cn(
          'flex h-8 items-center gap-1.5 rounded-md border bg-card pl-2.5 pr-2 font-mono text-caption tnum shadow-btn transition-colors duration-fast',
          open ? 'border-brand-400 text-brand-600' : 'border-line text-ink-600 hover:border-line-strong',
          triggerClassName,
        )}
      >
        {leading}
        <span>{current?.label ?? ''}</span>
        <Icon
          name="chevron-down"
          size={12}
          className={cn('shrink-0 text-ink-400 transition-transform duration-200', open && 'rotate-180')}
        />
      </button>
      {mounted && (
        <div
          role="listbox"
          aria-label={ariaLabel}
          data-origin={align === 'right' ? 'top-right' : 'top-left'}
          className={cn(
            't-dropdown absolute top-9 z-40 min-w-full whitespace-nowrap rounded-md border border-line bg-card p-1.5 shadow-sh-2',
            align === 'right' ? 'right-0' : 'left-0',
            overlayClassName(phase),
          )}
        >
          <div className="max-h-64 overflow-y-auto">
            {options.map((o) => {
              const active = o.value === value;
              return (
                <button
                  key={String(o.value)}
                  type="button"
                  role="option"
                  aria-selected={active}
                  onClick={() => {
                    onChange(o.value);
                    setOpen(false);
                  }}
                  className={cn(
                    'flex w-full items-center justify-between gap-3 rounded-xs px-2 py-1.5 text-left font-mono text-caption tnum transition-colors',
                    active ? 'bg-brand-50 text-brand-600' : 'text-ink-600 hover:bg-paper-2',
                  )}
                >
                  {o.label}
                  {active && <Icon name="check" size={12} className="shrink-0" />}
                </button>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}
