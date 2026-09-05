/**
 * 全部词典的合并入口。域文件按「中文原文 msgid → [en, ja]」组织（见 types.ts），
 * 合并策略是纯对象展开——同一个 msgid 在多个域文件里出现是允许的（内容寻址，
 * 天然去重），只要值一致；i18n-coverage.test.mjs 会断言不存在「同 msgid 不同译文」
 * 的冲突，这里不需要再做运行时检查。
 *
 * 全部用完整扩展名导入：node --experimental-strip-types 跑测试时按 ES 模块规范
 * 解析相对路径，不会像 Vite 那样自动补 .ts。
 */
import { QUOTES } from './quotes.ts';
import { CHROME } from './chrome.ts';
import { HINTS } from './hints.ts';
import { COMPANIES } from './companies.ts';
import { MACRO } from './macro.ts';
import { MARKET } from './market.ts';
import { SCREENER } from './screener.ts';
import { BREAKOUTS } from './breakouts.ts';
import { SECTORS } from './sectors.ts';
import { EARNINGS } from './earnings.ts';
import { CATALYSTS } from './catalysts.ts';
import { DETAIL } from './detail.ts';
import { ACCOUNT } from './account.ts';
import { WATCHLIST } from './watchlist.ts';
import { WIRED } from './wired.ts';
import { MOCKS } from './mocks.ts';
import { DRAWINGS } from './drawings.ts';
import { SMART_DRAWINGS } from './smartDrawings.ts';
import { OPTIONS_UI } from './optionsUi.ts';
import { STOCK_DATA } from './stockData.ts';
import type { Dict } from './types.ts';

export const DICT: Dict = {
  ...QUOTES,
  ...CHROME,
  ...HINTS,
  ...COMPANIES,
  ...MACRO,
  ...MARKET,
  ...SCREENER,
  ...BREAKOUTS,
  ...SECTORS,
  ...EARNINGS,
  ...CATALYSTS,
  ...DETAIL,
  ...ACCOUNT,
  ...WATCHLIST,
  ...WIRED,
  ...MOCKS,
  ...DRAWINGS,
  ...SMART_DRAWINGS,
  ...OPTIONS_UI,
  ...STOCK_DATA,
};
