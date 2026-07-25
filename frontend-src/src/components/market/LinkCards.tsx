/**
 * B6 联动卡：板块透视 / 突破雷达 入口（hover 上浮）
 */
import { Link } from 'react-router';
import Icon, { type IconName } from '@/components/icons';

const CARDS: { to: string; icon: IconName; title: string; en: string; desc: string }[] = [
  {
    to: '/sectors',
    icon: 'layers',
    title: '板块透视',
    en: 'SECTORS',
    desc: '十一大板块热力矩阵与 IV 排名，看资金在哪个赛道抱团。',
  },
  {
    to: '/breakouts',
    icon: 'radar',
    title: '突破雷达',
    en: 'BREAKOUT RADAR',
    desc: '当日突破信号与事件时间线，追踪生命周期状态迁移。',
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
            <span className="min-w-0 flex-1">
              <span className="flex items-baseline gap-2">
                <span className="text-h3 text-ink-900">{c.title}</span>
                <span className="eyebrow">{c.en}</span>
              </span>
              <span className="mt-1 block truncate text-caption text-ink-500">{c.desc}</span>
            </span>
            <Icon
              name="arrow-up-right"
              size={16}
              className="shrink-0 text-ink-300 transition-[transform,color] duration-fast group-hover:-translate-y-0.5 group-hover:translate-x-0.5 group-hover:text-brand-600"
            />
          </Link>
        </div>
      ))}
    </div>
  );
}
