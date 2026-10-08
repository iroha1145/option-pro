/**
 * 二级页面切换：选股（条件选股、突破雷达）与市场（美股概况、行业表现、CTA 趋势资金）。
 * 外观沿用分段控件，和新闻页的栏目切换同一副样子；语义是页面导航——
 * nav + 链接 + aria-current，不是 tablist。每页重新挂载，选中底块不做滑行。
 */
import { useLayoutEffect, useRef } from 'react';
import { Link, useLocation } from 'react-router';
import { cn, isNavPathActive } from '@/lib/utils';
import { navSectionPages, type NavSection } from '@/lib/navigation';
import { routeIntentHandlers } from '@/lib/prefetchRouteChunk';
import { t } from '../../i18n/core.ts';

const SECTION_LABEL: Record<NavSection, string> = {
  screen: t('选股页面'),
  market: t('市场页面'),
};

export default function SectionNav({ section, className }: { section: NavSection; className?: string }) {
  const { pathname } = useLocation();
  const viewportRef = useRef<HTMLDivElement>(null);
  /* 窄屏（尤其英日文）三项放不下时可横向滚动：把当前页滚进视野，别让「CTA trend」停在右边看不见 */
  useLayoutEffect(() => {
    const viewport = viewportRef.current;
    const current = viewport?.querySelector<HTMLElement>('[aria-current="page"]');
    if (!viewport || !current || viewport.scrollWidth <= viewport.clientWidth) return;
    const box = viewport.getBoundingClientRect();
    const item = current.getBoundingClientRect();
    if (item.left >= box.left && item.right <= box.right) return;
    viewport.scrollLeft += item.left - box.left - (box.width - item.width) / 2;
  }, [pathname]);
  return (
    <nav aria-label={SECTION_LABEL[section]} className={cn('mb-5', className)}>
      <div ref={viewportRef} className="selection-viewport no-scrollbar">
        <div className="t-tabs selection-group border border-line">
          {navSectionPages(section).map((page) => {
            const active = isNavPathActive(pathname, page.path);
            return (
              <div key={page.path} className="relative">
                {active && <span aria-hidden="true" className="selection-indicator pointer-events-none absolute inset-0" />}
                <Link
                  to={page.path}
                  aria-current={active ? 'page' : undefined}
                  className="t-tab relative z-10 inline-flex items-center whitespace-nowrap text-caption font-medium focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-600"
                  {...routeIntentHandlers(page.path)}
                >
                  {page.label}
                </Link>
              </div>
            );
          })}
        </div>
      </div>
    </nav>
  );
}
