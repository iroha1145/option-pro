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
        'flex h-8 items-center gap-1.5 rounded-md border border-line bg-card-warm px-2 text-caption shadow-btn transition-colors duration-fast hover:border-line-strong hover:text-ink-800',
        className,
      )}
    >
      {/* 点色走 up/down token：换盘后 CSS 变量对调，不必再反转 class。 */}
      <span className="flex items-center gap-0.5">
        <span className="size-2 rounded-full bg-up-600" />
        <span className="size-2 rounded-full bg-down-600" />
      </span>
      <span className="font-mono text-[11px] text-ink-600 tnum">
        {asian ? t('红涨') : t('绿涨')}
      </span>
    </button>
  );
}
