/** SessionLED：市场时段 LED（§1.6 色 + led-pulse），配文字（§10） */
import { cn } from '@/lib/utils';
import type { MarketSession } from '@/api/types';
import { t } from '../../i18n/core.ts';

const SESSION_STYLE: Record<MarketSession, { dot: string; text: string }> = {
  premarket: { dot: 'bg-warn-600', text: t('盘前') },
  regular: { dot: 'bg-up-600', text: t('盘中') },
  afterhours: { dot: 'bg-ai-600', text: t('盘后') },
  closed: { dot: 'bg-ink-400', text: t('休市') },
};

/**
 * `session === null` 表示「时段读不到」（状态接口未返回或失败）。它与「休市」
 * 是两回事：休市会改变用户对盘中信号的解读，所以未知态用更浅的灰点、不闪烁、
 * 文字明说未知（审计 P2-9 / 2026-07-27 审计 2.2.1）。
 */
export function SessionDot({ session, className }: { session: MarketSession | null; className?: string }) {
  return (
    <span
      className={cn(
        'inline-block size-2 rounded-full',
        session === null ? 'bg-ink-300' : SESSION_STYLE[session].dot,
        session !== null && session !== 'closed' && 'animate-led-pulse',
        className,
      )}
      aria-hidden="true"
    />
  );
}

export default function SessionLED({
  session,
  label,
  className,
  showLabel = true,
  loading = false,
}: {
  session: MarketSession | null;
  label?: string;
  className?: string;
  showLabel?: boolean;
  /** 状态接口仍在首取（仅影响未知态的文字） */
  loading?: boolean;
}) {
  if (session === null) {
    return (
      <span className={cn('inline-flex items-center gap-1.5', className)}>
        <SessionDot session={null} />
        {showLabel && (
          <span className="text-caption text-ink-400">
            {loading ? t('时段读取中…') : t('时段未知')}
          </span>
        )}
      </span>
    );
  }
  const s = SESSION_STYLE[session];
  return (
    <span className={cn('inline-flex items-center gap-1.5', className)}>
      <SessionDot session={session} />
      {showLabel && <span className="text-caption text-ink-500">{label ?? s.text}</span>}
    </span>
  );
}
