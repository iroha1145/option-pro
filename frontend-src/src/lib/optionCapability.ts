/**
 * Yahoo/yfinance 期权能力（与 backend/app/services/option_capability.py 同步）。
 * 先统一行情代码，再判断当前适配能否提供期权。不是「该指数在市场上没有期权」。
 */
import { quoteSymbol } from './quoteSymbol';

export const OPTION_CAPABILITY_VERSION = 'yahoo-index-quotes-v1';
export const OPTION_PROVIDER_YAHOO = 'Yahoo/yfinance';

export const YAHOO_UNSUPPORTED_QUOTE_SYMBOLS = [
  '^GSPC',
  '^IXIC',
  '^DJI',
  '^N225',
  '000001.SS',
] as const;

export type OptionCapabilityStatus =
  | 'supported'
  | 'unsupported_by_provider'
  | 'permission_denied'
  | 'unknown'
  | 'empty_unconfirmed'
  | 'temporary_unavailable';

export interface OptionCapability {
  ticker: string;
  provider: string;
  optionsStatus: OptionCapabilityStatus;
  retryable: boolean;
  capabilityVersion: string;
}

export function resolveOptionCapability(ticker: string): OptionCapability {
  const symbol = quoteSymbol(ticker);
  if ((YAHOO_UNSUPPORTED_QUOTE_SYMBOLS as readonly string[]).includes(symbol)) {
    return {
      ticker: symbol,
      provider: OPTION_PROVIDER_YAHOO,
      optionsStatus: 'unsupported_by_provider',
      retryable: false,
      capabilityVersion: OPTION_CAPABILITY_VERSION,
    };
  }
  return {
    ticker: symbol,
    provider: OPTION_PROVIDER_YAHOO,
    optionsStatus: 'unknown',
    retryable: true,
    capabilityVersion: OPTION_CAPABILITY_VERSION,
  };
}

export function isDeclaredUnsupported(ticker: string): boolean {
  return resolveOptionCapability(ticker).optionsStatus === 'unsupported_by_provider';
}
