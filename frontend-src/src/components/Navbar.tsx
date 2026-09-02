/**
 * Header（design.md §7.1）· sticky top-0 z-50 · 毛玻璃
 * Logo | 01–06 编号导航（滑动下划线） | ⌘K 触发 | 时段LED+纽约时钟 | AI 点（owner）| 登录/退出
 * 移动端折叠为 48px：Logo + ⌘K + 时钟。
 */
import { useLayoutEffect, useRef, useState } from 'react';
import { Link, NavLink, useLocation, useNavigate } from 'react-router';
import { cn } from '@/lib/utils';
import { useNow } from '@/hooks/useNow';
import { useAccess } from '@/hooks/useAccess';
import { useToast } from '@/components/Toast';
import { marketApi } from '@/api/modules/market';
import { usePolling } from '@/hooks/usePolling';
import { fmtNyTime } from '@/lib/format';
import { placeGlide } from '@/lib/transitions';
import Icon from '@/components/icons';
import { SessionDot } from '@/components/shared/SessionLED';
import LanguageSwitcher from '@/components/LanguageSwitcher';
import ColorModeSwitcher from '@/components/ColorModeSwitcher';
import { t } from '../i18n/core.ts';

export const NAV_ITEMS = [
  { no: '01', label: t('首页'), path: '/' },
  { no: '02', label: t('自选'), path: '/watchlist' },
  { no: '03', label: t('选股'), path: '/screener' },
  { no: '04', label: t('雷达'), path: '/breakouts' },
  { no: '05', label: t('板块'), path: '/sectors' },
  { no: '06', label: t('财报'), path: '/earnings' },
  { no: '07', label: t('催化'), path: '/catalysts' },
  /* 大盘强弱页此前没有任何常规入口（不在导航、不在 Dock、⌘K 也搜不到，
     唯一通路是点指数跑马灯）——一个完整页面不该只有彩蛋入口。 */
  { no: '08', label: t('大盘'), path: '/market' },
  /* CTA 趋势资金：原大盘页 B4.5 卡剥离成的独立页面 */
  { no: '09', label: t('CTA'), path: '/cta' },
] as const;

function NyClock({ className }: { className?: string }) {
  const now = useNow(1000);
  return (
    <span className={cn('font-mono text-micro text-ink-500 tnum', className)} suppressHydrationWarning>
      {fmtNyTime(new Date(now))} ET
    </span>
  );
}

export default function Navbar({ onOpenPalette }: { onOpenPalette: () => void }) {
  const { isOwner, isSignedIn, username, aiEnabled, aiAvailable, aiReason, logout } = useAccess();
  const toast = useToast();
  const navigate = useNavigate();
  const location = useLocation();
  const { data: status } = usePolling(() => marketApi.status(), 60_000);
  // 读不到时段时点是浅灰「未知」而不是「休市」（与审计 2.2.1 同根因）
  const session = status?.session ?? null;


  const navRef = useRef<HTMLElement>(null);
  const glideRef = useRef<HTMLSpanElement>(null);
  const glideReadyRef = useRef(false);
  const [loggingOut, setLoggingOut] = useState(false);
  const activePath = NAV_ITEMS.find((item) =>
    item.path === '/' ? location.pathname === '/' : location.pathname.startsWith(item.path),
  )?.path ?? '';

  const alignRef = useRef<(animate: boolean) => void>(() => undefined);
  useLayoutEffect(() => {
    const nav = navRef.current;
    const bar = glideRef.current;
    if (!nav || !bar) return;
    alignRef.current = (animate: boolean) => {
      const label = nav.querySelector('[data-active="true"] [data-nav-label]') as HTMLElement | null;
      if (!label) {
        bar.style.width = '0px';
        return;
      }
      // 相对 nav 盒测量：整条导航被右侧簇推移时偏移自消，不再靠像素绝对值。
      const navBox = nav.getBoundingClientRect();
      const box = label.getBoundingClientRect();
      placeGlide(bar, { offset: box.left - navBox.left, size: box.width }, { axis: 'x', animate });
    };
    /* ResizeObserver 只挂一次：每次 observe() 都会在同一渲染帧投递一次初始观察，
       若随 activePath 重建，那次初始回调会紧跟 align(true) 用 transition:none 把刚起步
       的补间当帧掐断——「滑行」永远只是瞬移（复审实锤）。首帧投递也要跳过：初始
       定位由下方按 activePath 的 effect 负责。nav 是内容定宽的 flex 盒，字体加载、
       2xl 编号出现、语言切换都会改它的尺寸而触发 RO；整体位移不改尺寸也不需要重对齐。 */
    let primed = false;
    const ro = new ResizeObserver(() => {
      if (!primed) {
        primed = true;
        return;
      }
      alignRef.current(false);
    });
    ro.observe(nav);
    return () => ro.disconnect();
  }, []);
  useLayoutEffect(() => {
    alignRef.current(glideReadyRef.current);
    glideReadyRef.current = true;
  }, [activePath]);
  const handleLogout = async () => {
    if (loggingOut) return;
    setLoggingOut(true);
    try {
      await logout();
      toast.info(
        isOwner ? t('已退出 Owner 模式') : t('已退出登录'),
        t('当前为访客只读模式'),
      );
      navigate('/watchlist');
    } catch (error) {
      // 登出失败以前完全静默：按钮按了没反应，会话还挂着
      toast.error(t('退出失败'), error instanceof Error ? error.message : t('请稍后再试'));
    } finally {
      setLoggingOut(false);
    }
  };

  return (
    <header className="glass sticky top-0 z-50 border-b border-line">
      <div className="mx-auto flex h-12 max-w-shell items-center gap-3 px-4 md:h-16 md:gap-5 md:px-8">
        {/* Logo */}
        <Link to="/" className="flex shrink-0 items-center gap-2.5" aria-label={t("Optix Pro 首页")}>
          <img src="/logo.svg" alt="" className="size-7 md:size-8" />
          <span className="hidden flex-col leading-none sm:flex">
            <span className="font-display text-[17px] font-bold text-ink-900">Optix Pro</span>
            <span className="eyebrow mt-0.5 text-[9px]">US EQUITY DESK</span>
          </span>
        </Link>

        {/* 编号导航（桌面） */}
        <nav
          ref={navRef}
          className="relative mx-auto hidden h-full items-center gap-1 xl:flex"
          aria-label={t("主导航")}
        >
          <span ref={glideRef} data-nav-glide="" aria-hidden="true" className="nav-glide" />
          {NAV_ITEMS.map((item) => {
            /* '/' 必须精确匹配：startsWith('/') 对任何路径都为真，首页会永远亮着 */
            const active = item.path === '/' ? location.pathname === '/' : location.pathname.startsWith(item.path);
            return (
            <NavLink
              key={item.path}
              to={item.path}
              data-active={active}
              className={({ isActive }) =>
                cn(
                  /* R4 加到 9 项后 1440(xl) 逼近满宽：sub-2xl 收 px-2，登录态
                     右侧簇（AI 胶囊+退出）才不会被挤出视口；≥2xl 恢复 3.5。 */
                  'flex h-full items-center gap-1.5 whitespace-nowrap px-2 text-body-s transition-colors duration-fast 2xl:px-3.5',
                  isActive ? 'font-medium text-brand-600' : 'text-ink-500 hover:text-ink-800',
                )
              }
            >
              {/* 9 项编号在 xl–2xl 之间是压垮布局的最后一根稻草（1440 登录态
                  「退出」被挤出视口）：sub-2xl 只留文字标签，≥2xl 恢复编号。 */}
              <span className="hidden font-mono text-[11px] text-ink-400 2xl:inline">{item.no}</span>
              {/* 标签盒是滑行下划线的测量锚：placeGlide 按它的 left/width 补间，
                  跨项滑动（beUI tabs / transitions.dev tabs-sliding）。 */}
              <span data-nav-label className="relative flex h-full items-center">
                {item.label}
              </span>
            </NavLink>
            );
          })}
        </nav>

        {/* 右侧操作区 */}
        <div className="ml-auto flex items-center gap-2.5 md:gap-3.5 xl:ml-0">
          <button
            onClick={onOpenPalette}
            /* xl–2xl 是 9 项导航的拥挤带（审计：1280 + 长用户名/英日文风险）：
               该档只留搜索图标（下方按钮），文字框在 md–xl 与 ≥2xl 显示。 */
            className="hidden h-8 w-44 items-center gap-2 rounded-md border border-line bg-card-warm px-3 text-caption text-ink-400 transition-[border-color,box-shadow,color] duration-fast hover:border-line-strong hover:text-ink-500 focus-visible:border-brand-500 focus-visible:shadow-focus-ring md:flex xl:hidden 2xl:flex 2xl:w-[220px]"
            aria-label={t("打开命令面板")}
          >
            <Icon name="search" size={14} />
            <span className="flex-1 truncate text-left">{t('搜索代码或功能…')}</span>
            <kbd className="flex items-center gap-0.5 font-mono text-[10px] text-ink-400">
              <Icon name="command" size={11} />K
            </kbd>
          </button>
          <button
            onClick={onOpenPalette}
            className="flex size-9 items-center justify-center rounded-md border border-line bg-card-warm text-ink-500 shadow-btn md:hidden xl:flex 2xl:hidden"
            aria-label={t("搜索")}
          >
            <Icon name="search" size={16} />
          </button>

          <span className="hidden items-center gap-2 md:flex" aria-label={t('市场时段：{label}', { label: status?.label ?? t('未知') })}>
            <SessionDot session={session} />
            <NyClock />
          </span>
          <span className="flex items-center gap-1.5 md:hidden">
            <SessionDot session={session} />
          </span>

          {isOwner && (
            <span
              className={cn(
                'hidden items-center gap-1.5 rounded-pill border px-2 py-0.5 text-micro md:flex',
                aiAvailable
                  ? 'border-ai-600/20 bg-ai-50 text-ai-600'
                  : aiEnabled
                    ? 'border-warn-600/25 bg-warn-50 text-warn-600'
                    : 'border-line bg-card-warm text-ink-400',
              )}
              title={
                aiAvailable
                  ? t('分析服务可用')
                  : aiEnabled && ['analysis_in_progress', 'global_concurrency_limit', 'queue_busy'].includes(aiReason ?? '')
                    ? t('分析任务处理中')
                    : aiEnabled
                      ? t('分析服务暂不可用')
                      : t('分析服务未开启')
              }
            >
              <Icon name="spark-ai" size={12} />
              AI
            </span>
          )}

          <LanguageSwitcher className="hidden md:block" />
          <ColorModeSwitcher className="hidden xl:flex" />

          {isSignedIn ? (
            <button
              onClick={handleLogout}
              disabled={loggingOut}
              className="flex h-8 max-w-[140px] shrink-0 items-center gap-1.5 whitespace-nowrap rounded-md border border-line bg-card px-3 text-caption text-ink-500 shadow-btn transition-colors hover:text-ink-800 disabled:cursor-wait disabled:opacity-60 md:max-w-none"
            >
              <Icon name="logout" size={14} className="shrink-0" />
              <span className="truncate">{username ? t('退出 {name}', { name: username }) : t('退出')}</span>
            </button>
          ) : (
            <Link
              to="/login"
              className="flex h-8 shrink-0 items-center whitespace-nowrap rounded-md bg-brand-600 px-3.5 text-caption font-medium text-white shadow-btn-hi transition-[transform,background-color] duration-fast hover:bg-brand-700 active:scale-[0.98]"
            >
              {t('登录')}
            </Link>
          )}
        </div>
      </div>
    </header>
  );
}
