/**
 * 语言切换器（design.md §7.1 附 + transitions.dev menu dropdown）
 * 桌面端挂在页头右侧操作区、登录/退出按钮左边；
 * 移动端由 MobileDock 的「更多」抽屉复用同一组件。
 * 选择后 setLocale() 落盘 + 整页重载，不在这里做任何「立即生效」的局部渲染。
 */
import { useEffect, useRef, useState } from 'react';
import { cn } from '@/lib/utils';
import {
  overlayClassName,
  overlayVisible,
  readRootDurationMs,
  useOverlayPhase,
} from '@/lib/transitions';
import { LOCALES, getLocale, setLocale } from '../i18n/core.ts';
import { t } from '../i18n/core.ts';
import Icon from '@/components/icons';

export default function LanguageSwitcher({ className }: { className?: string }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const listRef = useRef<HTMLUListElement>(null);
  const current = getLocale();
  const closeMs = readRootDurationMs('--dropdown-close-dur', 150);
  const phase = useOverlayPhase(open, closeMs);
  const mounted = overlayVisible(open, phase);

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

  const currentMeta = LOCALES.find((l) => l.code === current) ?? LOCALES[0];

  return (
    <div ref={ref} className={cn('relative', className)}>
      <button
        ref={triggerRef}
        onClick={() => setOpen((v) => !v)}
        onKeyDown={(e) => {
          if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
            e.preventDefault();
            setOpen(true);
          }
        }}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label={t('切换界面语言')}
        className={cn(
          'flex h-8 items-center gap-1.5 rounded-md border px-2.5 text-caption shadow-btn transition-colors duration-fast',
          open ? 'border-brand-400 text-brand-600' : 'border-line bg-card-warm text-ink-500 hover:text-ink-800',
        )}
      >
        <Icon name="languages" size={14} />
        <span className="font-mono text-[11px] tnum">{currentMeta.short}</span>
      </button>
      {mounted && (
        <div
          role="menu"
          aria-label={t('界面语言')}
          data-origin="top-right"
          className={cn(
            't-dropdown absolute right-0 top-10 z-40 w-[176px] rounded-md border border-line bg-card p-1.5 shadow-sh-2',
            overlayClassName(phase),
          )}
        >
          <p className="px-2 pb-1.5 pt-1 eyebrow">{t('界面语言')}</p>
          <ul ref={listRef} onKeyDown={onMenuKeyDown}>
            {LOCALES.map((l) => {
              const active = l.code === current;
              return (
                <li key={l.code}>
                  <button
                    role="menuitemradio"
                    aria-checked={active}
                    onClick={() => {
                      setOpen(false);
                      setLocale(l.code);
                    }}
                    className={cn(
                      'flex w-full items-center gap-2 rounded-xs px-2 py-1.5 text-left text-body-s transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500/30',
                      active ? 'text-brand-600' : 'text-ink-700 hover:bg-paper-2',
                    )}
                  >
                    <span className="w-4 shrink-0 font-mono text-[10px] text-ink-400">{l.short}</span>
                    <span className="flex-1">{l.native}</span>
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
