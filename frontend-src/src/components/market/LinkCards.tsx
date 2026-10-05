/**
 * B6 联动卡：板块透视 / 突破雷达 入口（hover 上浮）
 */
import { Link } from 'react-router';
import Icon, { type IconName } from '@/components/icons';
import { t } from '../../i18n/core.ts';

const CARDS: { to: string; icon: IconName; title: string }[] = [
  {
    to: '/sectors',
    icon: 'layers',
    title: t('板块透视'),
  },
  {
    to: '/breakouts',
    icon: 'radar',
    title: t('突破雷达'),
  },
];

export default function LinkCards() {
  return (
    <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
      {/* 后续区块 rise-in 减量：直接呈现 */}
      {CARDS.map((c) => (
        <div key={c.to}>
          <Link to={c.to} className="card-surface card-lift group flex items-center gap-4 p-5">
            <span className="flex size-11 shrink-0 items-center justify-center rounded-lg border border-line bg-brand-50 text-brand-600">
              <Icon name={c.icon} size={20} />
            </span>
            <span className="min-w-0 flex-1 text-h3 text-ink-900">{c.title}</span>
            <Icon
              name="arrow-up-right"
              size={16}
              className="shrink-0 text-ink-400 transition-[transform,color] duration-fast ease-paper group-hover:-translate-y-0.5 group-hover:translate-x-0.5 group-hover:text-brand-600"
            />
          </Link>
        </div>
      ))}
    </div>
  );
}
