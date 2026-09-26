/** EmptyState：手绘插画 + H3 + 说明 + 主按钮；503 变体附一行「稍后刷新再试」 */
import type { CSSProperties, ReactNode } from 'react';
import { cn } from '@/lib/utils';
import Icon from '@/components/icons';
import { t } from '../../i18n/core.ts';

interface EmptyStateProps {
  image?: string;          // public/ 手绘 SVG 路径
  icon?: 'doc-quote' | 'search';
  title: string;
  description?: string;
  action?: ReactNode;      // 主按钮
  footnote?: string;       // 访客提示等 Caption
  variant?: 'empty' | 'error';
  className?: string;
}

export default function EmptyState({ image, icon, title, description, action, footnote, variant = 'empty', className }: EmptyStateProps) {
  /* transitions.dev 18 texts-reveal 的入场半边：插画 → 标题 → 说明 → 操作依次上浮、
     去模糊（--stagger-* 令牌，40ms 一档）。空态常在加载结束的同一刻出现，
     一起硬切出来显得突兀；分层浮现让视线先落在标题上。 */
  let line = 0;
  const reveal = (): { className: string; style: CSSProperties } => ({
    className: 't-reveal-line',
    style: { '--line': line++ } as CSSProperties,
  });
  const art = reveal();
  const heading = reveal();
  const body = description ? reveal() : null;
  const note = variant === 'error' ? reveal() : null;
  const cta = action ? reveal() : null;
  const foot = footnote ? reveal() : null;
  return (
    <div className={cn('flex flex-col items-center px-6 py-12 text-center', className)}>
      {image ? (
        <img src={image} alt="" width={220} height={165} className={cn('mb-5 h-auto w-[220px] max-w-full opacity-95 dark:brightness-0 dark:invert dark:opacity-60', art.className)} style={art.style} loading="lazy" />
      ) : (
        <span className={cn('mb-4 flex size-14 items-center justify-center rounded-lg border border-line bg-card-warm text-ink-400', art.className)} style={art.style}>
          <Icon name={icon ?? 'doc-quote'} size={26} />
        </span>
      )}
      <h3 className={cn('text-h3 text-ink-800', heading.className)} style={heading.style}>{title}</h3>
      {body && <p className={cn('mt-1.5 max-w-[340px] text-body-s text-ink-500', body.className)} style={body.style}>{description}</p>}
      {note && (
        <p className={cn('mt-1 text-micro text-ink-400', note.className)} style={note.style}>{t('稍后刷新再试')}</p>
      )}
      {cta && <div className={cn('mt-5', cta.className)} style={cta.style}>{action}</div>}
      {foot && <p className={cn('mt-3 text-caption text-ink-400', foot.className)} style={foot.style}>{footnote}</p>}
    </div>
  );
}
