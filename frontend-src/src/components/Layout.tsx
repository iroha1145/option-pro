/**
 * Layout（design.md §7）
 * sticky Header + IndexTape + 内容槽（<Outlet/>，page-fade 转场）+ Footer
 * 全局：命令面板（⌘K）、移动 Dock、Toast。
 *
 * v2：个股详情从右侧抽屉改为 /stock/:ticker 全屏整页（参考日股工作台），
 * openTicker 一律导航——抽屉基座与 StockDrawerBody 已随之撤除。
 */
import { Suspense, useCallback, useEffect, useMemo, useState } from 'react';
import { Outlet, useLocation, useNavigate, useNavigationType } from 'react-router';
import Navbar from '@/components/Navbar';
import IndexTape from '@/components/IndexTape';
import Footer from '@/components/Footer';
import RouteErrorBoundary from '@/components/shared/RouteErrorBoundary';
import PageFallback from '@/components/shared/PageFallback';
import MobileDock from '@/components/MobileDock';
import CommandPalette from '@/components/CommandPalette';
import { pushRecent } from '@/lib/recentTickers';
import { ShellContext } from '@/hooks/useShell';
import { isMock } from '@/api/client';
import { t as __t } from '../i18n/core.ts';

export default function Layout() {
  const location = useLocation();
  const navigate = useNavigate();
  const navigationType = useNavigationType();
  const [paletteOpen, setPaletteOpen] = useState(false);

  const openPalette = useCallback(() => setPaletteOpen(true), []);
  const openTicker = useCallback((ticker: string) => {
    const symbol = ticker.toUpperCase();
    pushRecent(symbol);
    navigate(`/stock/${encodeURIComponent(symbol)}`);
  }, [navigate]);

  useEffect(() => {
    if (navigationType === 'POP') return;
    // A new page starts at its heading, even when opened from a long table.
    // Back/forward keep the browser's own restoration behavior.
    window.scrollTo({ top: 0, behavior: 'instant' });
    document.getElementById('main-content')?.focus({ preventScroll: true });
  }, [location.pathname, navigationType]);

  /* ⌘K / Ctrl+K 全局绑定 */
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.defaultPrevented || e.isComposing || e.keyCode === 229 || e.altKey || e.shiftKey) return;
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault();
        if (!e.repeat) setPaletteOpen((v) => !v);
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  const value = useMemo(() => ({ openPalette, openTicker }), [openPalette, openTicker]);

  return (
    <ShellContext.Provider value={value}>
      {/* overflow-x-clip：绝对定位的解释浮层即使处于 opacity-0 也占布局盒，
          窄屏时会把文档撑出横向滚动条。clip 只裁剪绘制，不建立滚动容器、
          不影响 sticky，也不裁剪 position:fixed 的 Dock；
          InfoHint 自身已把可见浮层收敛在视口内，因此这里裁不到真实内容。 */}
      <div className="flex min-h-[100dvh] flex-col overflow-x-clip">
        <a className="skip-link" href="#main-content">{__t('跳到主要内容')}</a>
        <Navbar onOpenPalette={openPalette} />
        <IndexTape />
        {isMock && (
          <aside className="border-b border-brand-100 bg-brand-50 px-4 py-2 text-center text-caption text-brand-700">
            {__t('演示模式 · 当前行情与信号为示例数据')}
          </aside>
        )}
        <main id="main-content" tabIndex={-1} className="mx-auto w-full max-w-shell flex-1 scroll-mt-24 px-4 pt-6 md:px-8 md:pt-8">
          {/* page-fade 转场改纯 CSS（enter 240ms opacity + translateY(6px)）。
              原先 AnimatePresence mode="wait" + initial opacity:0 意味着新页面
              「默认不可见、靠一帧 JS 动画亮起来」——催化这类重页面在低端手机上
              挂载时主线程被塞死，动画迟迟不跑，用户看到的就是内容已挂载的全白
              页（手机菜单进催化白屏的根因之二）。CSS 动画基态即可见：动画只
              描述过渡，被饿死时最坏也只是直接出现。代价是去掉了 120ms 的退场
              淡出。 */}
          <div key={location.pathname} className="page-enter">
            {/* 按路由重建的错误边界:页面崩溃显示错误卡而非白屏,切页自动复位 */}
            <RouteErrorBoundary>
              <Suspense fallback={<PageFallback />}>
                <Outlet />
              </Suspense>
            </RouteErrorBoundary>
          </div>
        </main>
        <Footer />
        <MobileDock />
      </div>

      <CommandPalette
        open={paletteOpen}
        onClose={() => setPaletteOpen(false)}
        onOpenTicker={openTicker}
        onForceRefresh={() => navigate('/watchlist?force=1')}
      />
    </ShellContext.Provider>
  );
}
