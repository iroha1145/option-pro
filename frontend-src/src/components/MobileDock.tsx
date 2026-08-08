/**
 * 移动端底部 Dock（design.md §7.4）
 * 悬浮胶囊：离屏 12px + safe-area、圆角毛玻璃、墨色浮起阴影；
 * 五个入口同级单色（雷达不再是中央凸起圆钮）；「更多」上弹 sheet（spring-gentle）。
 */
import { useEffect, useState, useRef } from 'react';
import { Link, useLocation, useNavigate } from 'react-router';
import { AnimatePresence, motion } from 'framer-motion';
import { cn } from '@/lib/utils';
import { useAccess } from '@/hooks/useAccess';
import { useFocusTrap } from '@/hooks/useFocusTrap';
import Icon, { type IconName } from '@/components/icons';
import Segmented from '@/components/shared/Segmented';
import { LOCALES, getLocale, setLocale, t } from '../i18n/core.ts';

/* setLocale() 整页重载才会切语言，模块级常量在加载期求值一次即可，不需要每次渲染重算 */
const DOCK_ITEMS: { label: string; path: string; icon: IconName }[] = [
  { label: t('首页'), path: '/', icon: 'candle' },
  { label: t('自选'), path: '/watchlist', icon: 'star-line' },
  { label: t('选股'), path: '/screener', icon: 'filter-funnel' },
  { label: t('雷达'), path: '/breakouts', icon: 'radar' },
];

const MORE_ITEMS: { label: string; path: string; icon: IconName; desc: string }[] = [
  /* 板块从 Dock 移入「更多」（首页/自选/选股/雷达占满四个一级入口） */
  { label: t('板块透视'), path: '/sectors', icon: 'layers', desc: t('主题板块热力与 IV 排名') },
  { label: t('财报日历'), path: '/earnings', icon: 'calendar-spark', desc: t('即将公布 × AI 影响') },
  { label: t('大盘强弱'), path: '/market', icon: 'radar', desc: t('指数 · 宽度 · 宏观环境') },
  { label: t('CTA 趋势资金'), path: '/cta', icon: 'candle', desc: t('趋势资金 · 触发位') },
  { label: t('新闻催化'), path: '/catalysts', icon: 'bolt', desc: t('热点 · 情绪新闻流') },
];

export default function MobileDock() {
  const location = useLocation();
  const navigate = useNavigate();
  const { isOwner, isSignedIn, username } = useAccess();
  const [moreOpen, setMoreOpen] = useState(false);
  /* aria-modal 配套（审计 #60）：声明了模态就要真的困住焦点，Drawer 与
     CommandPalette 都调了 useFocusTrap，这里补齐。 */
  const sheetRef = useRef<HTMLDivElement | null>(null);
  useFocusTrap(sheetRef, moreOpen);
  /* 导航离开时同步强制关闭：sheet 的卸载依赖 AnimatePresence 退场动画完成，
     而催化这类重页面挂载会持续榨干低端手机的主线程，退场被饿死时白色 sheet
     会一直盖在新页面上（手机「进催化白屏、刷新才好」的根因）。路由一变就
     立刻置 false，退场动画能跑就跑、跑不动也不影响下一帧强制清理。 */
  useEffect(() => {
    setMoreOpen(false);
  }, [location.pathname]);

  /* 「更多」sheet 声明了 aria-modal，就要有配套行为（审计 2.5.5）：
     Escape 可关 + 打开期间锁背景滚动。 */
  useEffect(() => {
    if (!moreOpen) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setMoreOpen(false);
    };
    document.addEventListener('keydown', onKey);
    document.body.style.overflow = 'hidden';
    return () => {
      document.removeEventListener('keydown', onKey);
      document.body.style.overflow = '';
    };
  }, [moreOpen]);

  const renderItem = (item: (typeof DOCK_ITEMS)[number]) => {
    /* '/' 必须精确匹配：startsWith('/') 对任何路径都为真，首页会永远亮着 */
    const active = item.path === '/' ? location.pathname === '/' : location.pathname.startsWith(item.path);
    return (
      <Link
        key={item.path}
        to={item.path}
        className="relative flex min-h-[44px] flex-1 flex-col items-center justify-center gap-1 transition-transform duration-fast active:scale-[0.96]"
        aria-label={item.label}
        aria-current={active ? 'page' : undefined}
      >
        {/* 当前页的小标记：图标上方一枚 4px 圆点，比整块高亮安静 */}
        <span
          aria-hidden="true"
          className={cn(
            'absolute top-1.5 size-1 rounded-full bg-brand-600 transition-opacity duration-fast',
            active ? 'opacity-100' : 'opacity-0',
          )}
        />
        <Icon name={item.icon} size={19} className={active ? 'text-brand-600' : 'text-ink-400'} />
        <span className={cn('text-[10px] leading-none', active ? 'font-medium text-brand-600' : 'text-ink-400')}>{item.label}</span>
      </Link>
    );
  };

  return (
    <>
      {/* 悬浮胶囊 Dock：离屏 12px、全圆角、毛玻璃 + 墨色浮起阴影；
          雷达与其余入口同级同色，不再做中央凸起圆钮。 */}
      <nav
        className="glass fixed inset-x-3 bottom-[calc(env(safe-area-inset-bottom)+0.75rem)] z-[60] mx-auto flex h-16 max-w-md items-stretch rounded-2xl border border-line px-1.5 shadow-dock xl:hidden"
        aria-label={t('移动端导航')}
      >
        {DOCK_ITEMS.map(renderItem)}
        <button
          onClick={() => setMoreOpen(true)}
          className="flex min-h-[44px] flex-1 flex-col items-center justify-center gap-1 transition-transform duration-fast active:scale-[0.96]"
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
              transition={{ duration: 0.15 }}
              /* 不用 backdrop-blur：全屏背板的实时模糊在移动 GPU 上是弹出/收起
                 掉帧的最大单项，纯色遮罩视觉上足够。 */
              className="fixed inset-0 z-[64] bg-[rgba(13,22,38,.32)] xl:hidden"
              onClick={() => setMoreOpen(false)}
            />
            <motion.div
              initial={{ y: '100%' }}
              animate={{ y: 0 }}
              /* 退场独立给 150ms（规则：退场 < 进场 220ms）：定长 tween 即使被饿死，
                 恢复的第一帧也已到终点；收起时拖沓的 sheet 会盖住新页面。 */
              exit={{ y: '100%', transition: { duration: 0.15, ease: [0.16, 1, 0.3, 1] } }}
              /* 短 tween 取代 spring：物理弹簧需要连续多帧收敛，主线程被重页面
                 挂载抢走时会长时间停在半途；定长 tween 即使被饿死，恢复的第一帧
                 也已到终点。 */
              transition={{ duration: 0.22, ease: [0.16, 1, 0.3, 1] }}
              ref={sheetRef}
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
                    className="flex w-full items-center gap-3 rounded-md px-3 py-3 text-left transition-[transform,background-color] hover:bg-paper-2 active:bg-line/60"
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
                  className="flex w-full items-center gap-3 rounded-md px-3 py-3 text-left transition-[transform,background-color] hover:bg-paper-2 active:bg-line/60"
                >
                  <span className={cn('flex size-9 items-center justify-center rounded-md border border-line', isOwner ? 'bg-up-50 text-up-700' : 'bg-card-warm text-ink-400')}>
                    <Icon name="shield" size={17} />
                  </span>
                  <span className="flex-1">
                    <span className="block text-body-s font-medium text-ink-800">
                      {isOwner ? t('Owner 已登录') : isSignedIn ? t('已登录 {name}', { name: username ?? '' }) : t('访客只读模式')}
                    </span>
                    <span className="block text-micro text-ink-400">
                      {isOwner ? t('可执行写操作') : isSignedIn ? t('自选保存在账号里') : t('登录后可强制刷新与 AI 分析')}
                    </span>
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
