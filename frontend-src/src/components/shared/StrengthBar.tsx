/**
 * StrengthBar（design.md §6-5）
 * 轨道 line 色 4px 圆角，填充 grow-bar；色阶 <50 ink-300 / 50–69 brand-400 / 70–84 brand-600 / ≥85 up-600
 */
import { cn } from '@/lib/utils';

export function strengthBarClass(score: number): string {
  if (score >= 85) return 'bg-up-600';
  if (score >= 70) return 'bg-brand-600';
  if (score >= 50) return 'bg-brand-400';
  return 'bg-ink-300';
}

export default function StrengthBar({
  score,
  width = 80,
  showScore = true,
  className,
}: {
  score: number | null | undefined;
  width?: number;
  showScore?: boolean;
  className?: string;
}) {
  /* live 无强度分（契约未覆盖）：空轨道 + 「—」，不编造 0 分 */
  const valid = typeof score === 'number' && Number.isFinite(score);
  return (
    <span className={cn('inline-flex items-center gap-2', className)} aria-label={valid ? `强度分 ${score}` : '强度分缺失'}>
      <span className="h-1 overflow-hidden rounded-pill bg-line" style={{ width }} role="presentation">
        {valid && (
          <span
            className={cn('block h-full origin-left rounded-pill animate-grow-bar', strengthBarClass(score))}
            style={{ width: `${Math.max(2, Math.min(100, score))}%` }}
          />
        )}
      </span>
      {showScore && <span className="font-mono text-[13px] leading-[18px] text-ink-600 tnum">{valid ? score : '—'}</span>}
    </span>
  );
}
