import { COMPANY_LOGO_ASSETS } from './companyLogoAssets.ts';
import { quoteSymbol } from './quoteSymbol.ts';

export function companySymbol(ticker: string): string {
  return quoteSymbol(ticker.trim().toUpperCase().replace(/^US\./, ''));
}

/**
 * 只用随前端发布的本地标志（public/static/company-logos，清单见 companyLogoAssets.ts），
 * 静态文件走 CDN 缓存。清单里没有的代码由调用方显示首字母，不再逐只请求标志接口：
 * 那条接口对访客只返回 503，每只还要等 300–600ms。
 */
export function companyLogoSource(ticker: string): string | null {
  const symbol = companySymbol(ticker);
  return Object.hasOwn(COMPANY_LOGO_ASSETS, symbol) ? COMPANY_LOGO_ASSETS[symbol] : null;
}
