import { COMPANY_LOGO_ASSETS } from './companyLogoAssets.ts';
import { quoteSymbol } from './quoteSymbol.ts';

export function companySymbol(ticker: string): string {
  return quoteSymbol(ticker.trim().toUpperCase().replace(/^US\./, ''));
}

/** Demo images stay local; live symbols use the same-origin image cache (disk + racing CDNs). */
export function companyLogoSources(ticker: string, mock: boolean): string[] {
  const symbol = companySymbol(ticker);
  if (!/^[A-Z0-9][A-Z0-9.-]{0,15}$/.test(symbol) || /[.-]$|\.\.|--/.test(symbol)) return [];
  const local = Object.hasOwn(COMPANY_LOGO_ASSETS, symbol) ? COMPANY_LOGO_ASSETS[symbol] : undefined;
  return [local, mock ? undefined : `/api/stocks/${encodeURIComponent(symbol)}/logo`]
    .filter((source): source is string => Boolean(source));
}
