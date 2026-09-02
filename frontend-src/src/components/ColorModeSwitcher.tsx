/**
 * 涨跌色彩模式切换器（美股绿涨红跌 / 亚洲红涨绿跌）
 */
import { useState } from 'react';
import { cn } from '@/lib/utils';
import { getColorMode, setColorMode, type ColorMode } from '@/lib/colorPreference.ts';
import { t } from '../i18n/core.ts';

export default function ColorModeSwitcher({ className }: { className?: string }) {
  const [mode, setMode] = useState<ColorMode>(() => getColorMode());

  const toggle = () => {
    const next = mode === 'western' ? 'asian' : 'western';
    setColorMode(next);
    setMode(next);
  };

  return (
    <button
      type="button"
      onClick={toggle}
      title={mode === 'western' ? t('当前：绿涨红跌（点击切换红涨绿跌）') : t('当前：红涨绿跌（点击切换绿涨红跌）')}
      aria-label={t('切换涨跌色彩模式')}
      className={cn(
        'flex h-8 items-center gap-1.5 rounded-md border border-line bg-card-warm px-2 text-caption shadow-btn transition-colors duration-fast hover:border-line-strong hover:text-ink-800',
        className,
      )}
    >
      <span className="flex items-center gap-0.5">
        <span className={cn('size-2 rounded-full', mode === 'western' ? 'bg-up-600' : 'bg-down-600')} />
        <span className={cn('size-2 rounded-full', mode === 'western' ? 'bg-down-600' : 'bg-up-600')} />
      </span>
      <span className="font-mono text-[11px] text-ink-600 tnum">
        {mode === 'western' ? t('绿涨') : t('红涨')}
      </span>
    </button>
  );
}
