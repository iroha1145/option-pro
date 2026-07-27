/** EmptyState：手绘插画 + H3 + 说明 + 主按钮；503 变体附一行「稍后刷新再试」 */
import type { ReactNode } from 'react';
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
  return (
    <div className={cn('flex flex-col items-center px-6 py-12 text-center', className)}>
      {image ? (
        <img src={image} alt="" width={220} height={165} className="mb-5 h-auto w-[220px] max-w-full opacity-95" loading="lazy" />
      ) : (
        <span className="mb-4 flex size-14 items-center justify-center rounded-lg border border-line bg-card-warm text-ink-400">
          <Icon name={icon ?? 'doc-quote'} size={26} />
        </span>
      )}
      <h3 className="text-h3 text-ink-800">{title}</h3>
      {description && <p className="mt-1.5 max-w-[340px] text-body-s text-ink-500">{description}</p>}
      {variant === 'error' && (
        <p className="mt-1 text-micro text-ink-400">{t('稍后刷新再试')}</p>
      )}
      {action && <div className="mt-5">{action}</div>}
      {footnote && <p className="mt-3 text-caption text-ink-400">{footnote}</p>}
    </div>
  );
}
