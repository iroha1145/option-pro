/**
 * 移动端底部 Dock（design.md §7.4）
 * 64px + safe-area 毛玻璃；中央雷达凸起 44px 圆钮；「更多」上弹 sheet（spring-gentle）。
 */
import { useState } from 'react';
import { Link, useLocation, useNavigate } from 'react-router';
import { AnimatePresence, motion } from 'framer-motion';
import { cn } from '@/lib/utils';
import { useAccess } from '@/hooks/useAccess';
import Icon, { type IconName } from '@/components/icons';
import Segmented from '@/components/shared/Segmented';
import { LOCALES, getLocale, setLocale, t } from '@/i18n/core';

/* setLocale() 整页重载才会切语言，模块级常量在加载期求值一次即可，不需要每次渲染重算 */
const DOCK_ITEMS: { label: string; path: string; icon: IconName }[] = [
  { label: t('自选'), path: '/watchlist', icon: 'star-line' },
  { label: t('选股'), path: '/screener', icon: 'filter-funnel' },
  { label: t('雷达'), path: '/breakouts', icon: 'radar' },
  { label: t('板块'), path: '/sectors', icon: 'layers' },
];

const MORE_ITEMS: { label: string; path: string; icon: IconName; desc: string }[] = [
  { label: t('财报日历'), path: '/earnings', icon: 'calendar-spark', desc: t('即将公布 × AI 影响') },
  { label: t('新闻催化'), path: '/catalysts', icon: 'bolt', desc: t('热点 · 情绪新闻流') },
];

export default function MobileDock() {
  const location = useLocation();
  const navigate = useNavigate();
  const { isOwner } = useAccess();
  const [moreOpen, setMoreOpen] = useState(false);

  const renderItem = (item: (typeof DOCK_ITEMS)[number]) => {
    const active = location.pathname.startsWith(item.path);
    if (item.path === '/breakouts') {
      // 中央凸起圆钮
      return (
        <Link
          key={item.path}
          to={item.path}
          className="relative flex flex-1 flex-col items-center justify-end pb-1.5"
          aria-label={item.label}
        >
          <span
            className={cn(
              'absolute -top-5 flex size-11 items-center justify-center overflow-hidden rounded-full text-white shadow-sh-2 transition-colors duration-fast',
              active ? 'bg-brand-700' : 'bg-brand-600',
            )}
          >
            {/* radar-sweep 微动画 */}
            <span
              aria-hidden="true"
              className="absolute inset-0 animate-radar-sweep"
              style={{ background: 'conic-gradient(from 0deg, rgba(255,255,255,.35), transparent 90deg)' }}
            />
            <Icon name="radar" size={20} className="relative" />
          </span>
          <span className={cn('mt-6 text-[10px] leading-none', active ? 'font-medium text-brand-600' : 'text-ink-400')}>{item.label}</span>
        </Link>
      );
    }
    return (
      <Link
        key={item.path}
        to={item.path}
        className="flex min-h-[44px] flex-1 flex-col items-center justify-center gap-0.5"
        aria-label={item.label}
        aria-current={active ? 'page' : undefined}
      >
        <Icon name={item.icon} size={19} className={active ? 'text-brand-600' : 'text-ink-400'} />
        <span className={cn('text-[10px] leading-none', active ? 'font-medium text-brand-600' : 'text-ink-400')}>{item.label}</span>
      </Link>
    );
  };

  return (
    <>
      <nav
        className="glass fixed inset-x-0 bottom-0 z-[60] flex h-[calc(4rem+env(safe-area-inset-bottom))] items-stretch border-t border-line px-2 pb-[env(safe-area-inset-bottom)] xl:hidden"
        aria-label={t('移动端导航')}
      >
        {DOCK_ITEMS.slice(0, 2).map(renderItem)}
        {renderItem(DOCK_ITEMS[2])}
        {renderItem(DOCK_ITEMS[3])}
        <button
          onClick={() => setMoreOpen(true)}
          className="flex min-h-[44px] flex-1 flex-col items-center justify-center gap-0.5"
          aria-label={t('更多')}
        >
          <Icon name="menu" size={19} className="text-ink-400" />
          <span className="text-[10px] leading-none text-ink-400">{t('更多')}</span>
        </button>
      </nav>

      {/* 「更多」上弹 sheet */}
      <AnimatePresence>
        {moreOpen && (
          <>
            <motion.div
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              transition={{ duration: 0.2 }}
              className="fixed inset-0 z-[64] bg-[rgba(13,22,38,.28)] backdrop-blur-[2px] xl:hidden"
              onClick={() => setMoreOpen(false)}
            />
            <motion.div
              initial={{ y: '100%' }}
              animate={{ y: 0 }}
              exit={{ y: '100%' }}
              transition={{ type: 'spring', stiffness: 300, damping: 30 }}
              className="fixed inset-x-0 bottom-0 z-[65] rounded-t-xl border-t border-line bg-card pb-[calc(env(safe-area-inset-bottom)+16px)] shadow-sh-3 xl:hidden"
              role="dialog"
              aria-modal="true"
              aria-label={t('更多功能')}
            >
              <div className="flex justify-center pb-1 pt-2 text-ink-300">
                <Icon name="dots-grid" size={18} />
              </div>
              <p className="eyebrow px-5 pb-2 pt-1">{t('更多功能')}</p>
              <div className="px-3">
                <div className="flex items-center justify-between gap-3 rounded-md px-3 py-3">
                  <span className="flex items-center gap-3">
                    <span className="flex size-9 items-center justify-center rounded-md border border-line bg-card-warm text-brand-600">
                      <Icon name="languages" size={17} />
                    </span>
                    <span className="text-body-s font-medium text-ink-800">{t('界面语言')}</span>
                  </span>
                  <Segmented
                    options={LOCALES.map((l) => ({ value: l.code, label: l.short }))}
                    value={getLocale()}
                    onChange={(code) => setLocale(code)}
                  />
                </div>
                <div className="mx-3 my-2 border-t border-line" />
                {MORE_ITEMS.map((m) => (
                  <button
                    key={m.path}
                    onClick={() => {
                      setMoreOpen(false);
                      navigate(m.path);
                    }}
                    className="flex w-full items-center gap-3 rounded-md px-3 py-3 text-left transition-colors hover:bg-paper-2"
                  >
                    <span className="flex size-9 items-center justify-center rounded-md border border-line bg-card-warm text-brand-600">
                      <Icon name={m.icon} size={17} />
                    </span>
                    <span className="flex-1">
                      <span className="block text-body-s font-medium text-ink-800">{m.label}</span>
                      <span className="block text-micro text-ink-400">{m.desc}</span>
                    </span>
                    <Icon name="chevron-right" size={14} className="text-ink-300" />
                  </button>
                ))}
                <div className="mx-3 my-2 border-t border-line" />
                <button
                  onClick={() => {
                    setMoreOpen(false);
                    navigate('/login');
                  }}
                  className="flex w-full items-center gap-3 rounded-md px-3 py-3 text-left transition-colors hover:bg-paper-2"
                >
                  <span className={cn('flex size-9 items-center justify-center rounded-md border border-line', isOwner ? 'bg-up-50 text-up-700' : 'bg-card-warm text-ink-400')}>
                    <Icon name="shield" size={17} />
                  </span>
                  <span className="flex-1">
                    <span className="block text-body-s font-medium text-ink-800">{isOwner ? t('Owner 已登录') : t('访客只读模式')}</span>
                    <span className="block text-micro text-ink-400">{isOwner ? t('可执行写操作') : t('登录后可强制刷新与 AI 分析')}</span>
                  </span>
                </button>
              </div>
            </motion.div>
          </>
        )}
      </AnimatePresence>
    </>
  );
}
