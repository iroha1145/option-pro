/**
 * 涨跌色彩模式切换器（美股绿涨红跌 / 亚洲红涨绿跌）
 */
import { cn } from '@/lib/utils';
import { setColorMode } from '@/lib/colorPreference.ts';
import { useColorMode } from '@/hooks/useColorMode.ts';
import { t } from '../i18n/core.ts';

export default function ColorModeSwitcher({ className }: { className?: string }) {
  const mode = useColorMode();
  const asian = mode === 'asian';

  return (
    <button
      type="button"
      onClick={() => setColorMode(asian ? 'western' : 'asian')}
      aria-pressed={asian}
      title={asian ? t('当前：红涨绿跌（点击切换绿涨红跌）') : t('当前：绿涨红跌（点击切换红涨绿跌）')}
      aria-label={t('切换涨跌色彩模式')}
      className={cn(
        'color-mode-control inline-flex h-8 shrink-0 items-center justify-center gap-1.5 whitespace-nowrap rounded-md border border-line bg-card px-2.5 text-ink-500 transition-colors duration-fast hover:bg-paper hover:text-ink-800 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ink-400',
        className,
      )}
    >
      <svg width="14" height="14" viewBox="0 0 20 20" fill="none" aria-hidden="true" className="shrink-0">
        <path d="M6 15V4m0 0L3 7m3-3 3 3M14 5v11m0 0 3-3m-3 3-3-3" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
      <span className="whitespace-nowrap text-[11px] font-medium leading-4">
        {asian ? t('红涨绿跌') : t('绿涨红跌')}
      </span>
    </button>
  );
}
