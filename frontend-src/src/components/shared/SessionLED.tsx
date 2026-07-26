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

export function SessionDot({ session, className }: { session: MarketSession; className?: string }) {
  return (
    <span
      className={cn('inline-block size-2 rounded-full', SESSION_STYLE[session].dot, session !== 'closed' && 'animate-led-pulse', className)}
      aria-hidden="true"
    />
  );
}

export default function SessionLED({
  session,
  label,
  className,
  showLabel = true,
}: {
  session: MarketSession;
  label?: string;
  className?: string;
  showLabel?: boolean;
}) {
  const s = SESSION_STYLE[session];
  return (
    <span className={cn('inline-flex items-center gap-1.5', className)}>
      <SessionDot session={session} />
      {showLabel && <span className="text-caption text-ink-500">{label ?? s.text}</span>}
    </span>
  );
}
