/**
 * 外观切换器：跟随系统 / 浅色 / 深色。
 * 桌面与手机都挂在页头右上角；登录页复用同一控件。
 */
import { useEffect, useRef, useState } from 'react';
import { cn } from '@/lib/utils';
import {
  overlayClassName,
  overlayVisible,
  readRootDurationMs,
  useOverlayPhase,
} from '@/lib/transitions';
import { setThemePreference, type ThemePreference } from '@/lib/themePreference.ts';
import { useAppearance, useThemePreference } from '@/hooks/useAppearance.ts';
import { t } from '../i18n/core.ts';
import Icon, { type IconName } from '@/components/icons';

const OPTIONS: { value: ThemePreference; label: string; icon: IconName }[] = [
  { value: 'system', label: t('跟随系统'), icon: 'display' },
  { value: 'light', label: t('浅色'), icon: 'sun-bmo' },
  { value: 'dark', label: t('深色'), icon: 'moon-amc' },
];

function preferenceLabel(preference: ThemePreference): string {
  if (preference === 'light') return t('浅色');
  if (preference === 'dark') return t('深色');
  return t('跟随系统');
}

export default function ThemeSwitcher({ className }: { className?: string }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const listRef = useRef<HTMLUListElement>(null);
  const preference = useThemePreference();
  const appearance = useAppearance();
  const closeMs = readRootDurationMs('--dropdown-close-dur', 150);
  const phase = useOverlayPhase(open, closeMs);
  const mounted = overlayVisible(open, phase);
  const triggerIcon: IconName = appearance === 'dark' ? 'moon-amc' : 'sun-bmo';

  useEffect(() => {
    if (!open) return;
    const target =
      listRef.current?.querySelector<HTMLButtonElement>('[role="menuitemradio"][aria-checked="true"]') ??
      listRef.current?.querySelector<HTMLButtonElement>('[role="menuitemradio"]');
    target?.focus();
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (!ref.current?.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        setOpen(false);
        triggerRef.current?.focus();
      }
    };
    document.addEventListener('mousedown', onDoc);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDoc);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);

  const onMenuKeyDown = (e: React.KeyboardEvent<HTMLUListElement>) => {
    const items = Array.from(
      listRef.current?.querySelectorAll<HTMLButtonElement>('[role="menuitemradio"]') ?? [],
    );
    if (!items.length) return;
    const idx = items.indexOf(document.activeElement as HTMLButtonElement);
    let target = -1;
    if (e.key === 'ArrowDown') target = Math.min(idx + 1, items.length - 1);
    else if (e.key === 'ArrowUp') target = Math.max(idx - 1, 0);
    else if (e.key === 'Home') target = 0;
    else if (e.key === 'End') target = items.length - 1;
    else return;
    e.preventDefault();
    items[target]?.focus();
  };

  return (
    <div ref={ref} className={cn('relative', className)}>
      <button
        ref={triggerRef}
        type="button"
        onClick={() => setOpen((v) => !v)}
        onKeyDown={(e) => {
          if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
            e.preventDefault();
            setOpen(true);
          }
        }}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label={t('切换外观')}
        title={t('当前外观：{mode}', { mode: preferenceLabel(preference) })}
        className={cn(
          'theme-switcher-control flex size-9 shrink-0 items-center justify-center rounded-md border shadow-btn transition-colors duration-fast md:h-8 md:w-8',
          open ? 'border-brand-400 text-brand-600' : 'border-line bg-card-warm text-ink-500 hover:text-ink-800',
        )}
      >
        <Icon name={triggerIcon} size={14} />
      </button>
      {mounted && (
        <div
          role="menu"
          aria-labelledby="theme-appearance-label"
          data-origin="top-right"
          className={cn(
            't-dropdown absolute right-0 top-10 z-40 w-[176px] rounded-md border border-line bg-card p-1.5 shadow-sh-2',
            overlayClassName(phase),
          )}
        >
          <p id="theme-appearance-label" className="eyebrow px-2 pb-1.5 pt-1">{t('外观')}</p>
          <ul ref={listRef} role="none" onKeyDown={onMenuKeyDown}>
            {OPTIONS.map((option) => {
              const active = option.value === preference;
              return (
                <li key={option.value} role="none">
                  <button
                    type="button"
                    role="menuitemradio"
                    aria-checked={active}
                    onClick={() => {
                      setThemePreference(option.value);
                      setOpen(false);
                      triggerRef.current?.focus();
                    }}
                    className={cn(
                      'flex w-full items-center gap-2 rounded-xs px-2 py-1.5 text-left text-body-s transition-colors focus-visible:bg-paper-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-600',
                      active ? 'text-brand-600' : 'text-ink-700 hover:bg-paper-2',
                    )}
                  >
                    <Icon name={option.icon} size={14} className="shrink-0 text-ink-400" />
                    <span className="flex-1">{option.label}</span>
                    {active && <Icon name="check" size={13} className="shrink-0" />}
                  </button>
                </li>
              );
            })}
          </ul>
        </div>
      )}
    </div>
  );
}
