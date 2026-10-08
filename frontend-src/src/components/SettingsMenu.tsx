/**
 * 页头设置菜单：界面语言、外观、涨跌颜色收进同一个入口（2026-10-08 第二版）。
 * 原来页头并排三个控件（语言、涨跌颜色、外观），与研究栏目抢位置；手机「更多」
 * 抽屉里还有一份。现在桌面和手机都只从这里改。
 *
 * 结构沿用原外观菜单：role="menu" + 三组 menuitemradio，方向键在全部选项间移动、
 * 回车或点击才生效——语言切换会整页重载，不能像分段控件那样焦点一到就切。
 */
import { useEffect, useRef, useState } from 'react';
import { cn } from '@/lib/utils';
import { overlayClassName, overlayVisible, readRootDurationMs, useOverlayPhase } from '@/lib/transitions';
import { setThemePreference, type ThemePreference } from '@/lib/themePreference.ts';
import { useThemePreference } from '@/hooks/useAppearance.ts';
import { setColorMode, type ColorMode } from '@/lib/colorPreference.ts';
import { useColorMode } from '@/hooks/useColorMode.ts';
import Icon, { type IconName } from '@/components/icons';
import { LOCALES, getLocale, setLocale, t } from '../i18n/core.ts';

const THEME_OPTIONS: { value: ThemePreference; label: string; icon: IconName }[] = [
  { value: 'system', label: t('跟随系统'), icon: 'display' },
  { value: 'light', label: t('浅色'), icon: 'sun-bmo' },
  { value: 'dark', label: t('深色'), icon: 'moon-amc' },
];

const COLOR_OPTIONS: { value: ColorMode; label: string }[] = [
  { value: 'western', label: t('绿涨红跌') },
  { value: 'asian', label: t('红涨绿跌') },
];

const ITEM_CLASS =
  'flex w-full items-center gap-2 rounded-xs px-2 py-1.5 text-left text-body-s transition-colors duration-fast focus-visible:bg-paper-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-600';

export default function SettingsMenu({ className }: { className?: string }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const locale = getLocale();
  const themePreference = useThemePreference();
  const colorMode = useColorMode();
  const closeMs = readRootDurationMs('--dropdown-close-dur', 150);
  const phase = useOverlayPhase(open, closeMs);
  const mounted = overlayVisible(open, phase);

  useEffect(() => {
    if (!open) return;
    const target =
      menuRef.current?.querySelector<HTMLButtonElement>('[role="menuitemradio"][aria-checked="true"]') ??
      menuRef.current?.querySelector<HTMLButtonElement>('[role="menuitemradio"]');
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

  const onMenuKeyDown = (e: React.KeyboardEvent<HTMLDivElement>) => {
    const items = Array.from(menuRef.current?.querySelectorAll<HTMLButtonElement>('[role="menuitemradio"]') ?? []);
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

  const choose = (apply: () => void) => {
    apply();
    setOpen(false);
    triggerRef.current?.focus();
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
        aria-label={t('显示设置')}
        title={t('显示设置')}
        /* 外层是透明触控区（手机 44px），看得见的 32px 按钮在 .header-chip；展开态由 aria-expanded 着色 */
        className="touch-target header-action"
      >
        <span className="header-chip">
          <Icon name="sliders" size={15} />
        </span>
      </button>
      {mounted && (
        <div
          ref={menuRef}
          role="menu"
          aria-label={t('显示设置')}
          data-origin="top-right"
          onKeyDown={onMenuKeyDown}
          className={cn(
            't-dropdown absolute right-0 top-full z-40 mt-2 w-[208px] rounded-md border border-line bg-card p-1.5 shadow-sh-2',
            overlayClassName(phase),
          )}
        >
          <div role="group" aria-labelledby="settings-locale-label">
            <p id="settings-locale-label" className="eyebrow px-2 pb-1 pt-1">{t('界面语言')}</p>
            {LOCALES.map((l) => {
              const active = l.code === locale;
              return (
                <button
                  key={l.code}
                  type="button"
                  role="menuitemradio"
                  aria-checked={active}
                  onClick={() => {
                    setOpen(false);
                    if (!active) setLocale(l.code);
                  }}
                  className={cn(ITEM_CLASS, active ? 'text-brand-600' : 'text-ink-700 hover:bg-paper-2')}
                >
                  <span className="w-5 shrink-0 tnum text-micro text-ink-400">{l.short}</span>
                  <span className="flex-1">{l.native}</span>
                  {active && <Icon name="check" size={13} className="shrink-0" />}
                </button>
              );
            })}
          </div>
          <div role="group" aria-labelledby="settings-theme-label" className="mt-1 border-t border-line pt-1">
            <p id="settings-theme-label" className="eyebrow px-2 pb-1 pt-1">{t('外观')}</p>
            {THEME_OPTIONS.map((option) => {
              const active = option.value === themePreference;
              return (
                <button
                  key={option.value}
                  type="button"
                  role="menuitemradio"
                  aria-checked={active}
                  onClick={() => choose(() => setThemePreference(option.value))}
                  className={cn(ITEM_CLASS, active ? 'text-brand-600' : 'text-ink-700 hover:bg-paper-2')}
                >
                  <Icon name={option.icon} size={14} className="w-5 shrink-0 text-ink-400" />
                  <span className="flex-1">{option.label}</span>
                  {active && <Icon name="check" size={13} className="shrink-0" />}
                </button>
              );
            })}
          </div>
          <div role="group" aria-labelledby="settings-color-label" className="mt-1 border-t border-line pt-1">
            <p id="settings-color-label" className="eyebrow px-2 pb-1 pt-1">{t('涨跌颜色')}</p>
            {COLOR_OPTIONS.map((option) => {
              const active = option.value === colorMode;
              return (
                <button
                  key={option.value}
                  type="button"
                  role="menuitemradio"
                  aria-checked={active}
                  onClick={() => choose(() => setColorMode(option.value))}
                  className={cn(ITEM_CLASS, active ? 'text-brand-600' : 'text-ink-700 hover:bg-paper-2')}
                >
                  {/* 两个色点按当前选项的含义排：左涨右跌 */}
                  <span className="flex w-5 shrink-0 items-center gap-0.5" aria-hidden="true">
                    <span className={cn('size-1.5 rounded-full', option.value === 'western' ? 'bg-ok-600' : 'bg-danger-600')} />
                    <span className={cn('size-1.5 rounded-full', option.value === 'western' ? 'bg-danger-600' : 'bg-ok-600')} />
                  </span>
                  <span className="flex-1">{option.label}</span>
                  {active && <Icon name="check" size={13} className="shrink-0" />}
                </button>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}
