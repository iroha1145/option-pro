/**
 * 全站导航结构（2026-10-08 第二版）
 *
 * 一级 6 项：首页、我的关注、选股、市场、财报、新闻。选股（条件选股、突破雷达）
 * 与市场（美股概况、行业表现、CTA 趋势）两组的子页面用页内二级标签切换，
 * 九个页面地址都不变。命令面板仍逐页列出全部页面（NAV_PAGES）。
 */
import { isNavPathActive } from './utils.ts';
import { t } from '../i18n/core.ts';

export interface NavPage {
  label: string;
  path: string;
}

export type NavSection = 'screen' | 'market';

export interface NavGroup {
  label: string;
  /** 点一级入口时进入的页面（组内第一页） */
  path: string;
  /** 二级页面；单页入口为空 */
  pages: readonly NavPage[];
  section?: NavSection;
}

const HOME: NavPage = { label: t('首页'), path: '/' };
const WATCHLIST: NavPage = { label: t('我的关注'), path: '/watchlist' };
const SCREENER: NavPage = { label: t('条件选股'), path: '/screener' };
const BREAKOUTS: NavPage = { label: t('突破雷达'), path: '/breakouts' };
const MARKET: NavPage = { label: t('美股概况'), path: '/market' };
const SECTORS: NavPage = { label: t('行业表现'), path: '/sectors' };
const CTA: NavPage = { label: t('CTA 趋势'), path: '/cta' };
const EARNINGS: NavPage = { label: t('财报日历'), path: '/earnings' };
const NEWS: NavPage = { label: t('新闻'), path: '/catalysts' };

export const NAV_PAGES: readonly NavPage[] = [HOME, WATCHLIST, SCREENER, BREAKOUTS, MARKET, SECTORS, CTA, EARNINGS, NEWS];

export const NAV_GROUPS: readonly NavGroup[] = [
  { label: t('首页'), path: HOME.path, pages: [] },
  { label: t('我的关注'), path: WATCHLIST.path, pages: [] },
  { label: t('选股'), path: SCREENER.path, pages: [SCREENER, BREAKOUTS], section: 'screen' },
  { label: t('市场'), path: MARKET.path, pages: [MARKET, SECTORS, CTA], section: 'market' },
  { label: t('财报'), path: EARNINGS.path, pages: [] },
  { label: t('新闻'), path: NEWS.path, pages: [] },
];

/** 一级入口高亮：带二级页面的组看任一子页，其余按自身路径（边界匹配同 isNavPathActive） */
export function isNavGroupActive(pathname: string, group: NavGroup): boolean {
  if (group.pages.length === 0) return isNavPathActive(pathname, group.path);
  return group.pages.some((page) => isNavPathActive(pathname, page.path));
}

export function navSectionPages(section: NavSection): readonly NavPage[] {
  return NAV_GROUPS.find((group) => group.section === section)?.pages ?? [];
}
