import { COMPANY_LOGO_ASSETS } from './companyLogoAssets.ts';
import { quoteSymbol } from './quoteSymbol.ts';

export function companySymbol(ticker: string): string {
  return quoteSymbol(ticker.trim().toUpperCase().replace(/^US\./, ''));
}

/**
 * 公司标志的图片地址。先查随前端发布的本地清单（public/static/company-logos，见 companyLogoAssets.ts，走 CDN 缓存）。
 * 清单外的代码只有登录会话才回退到标志接口：接口读的是后端缓存，对访客只回 503，
 * 所以访客和演示数据不发这条请求，由调用方显示首字母。
 */
export function companyLogoSource(ticker: string, session: { signedIn: boolean; mock: boolean }): string | null {
  const symbol = companySymbol(ticker);
  if (Object.hasOwn(COMPANY_LOGO_ASSETS, symbol)) return COMPANY_LOGO_ASSETS[symbol];
  if (!session.signedIn || session.mock) return null;
  if (!/^[A-Z0-9][A-Z0-9.-]{0,15}$/.test(symbol) || /[.-]$|\.\.|--/.test(symbol)) return null;
  return `/api/stocks/${encodeURIComponent(symbol)}/logo`;
}
