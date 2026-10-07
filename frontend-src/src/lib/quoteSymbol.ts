/** 展示指数别名 → 真实行情符号；纳指综合与纳指 100 保持独立。 */
const INDEX_ALIASES: Record<string, string> = {
  SPX: '^GSPC', GSPC: '^GSPC', '^SPX': '^GSPC',
  NDX: '^NDX', IXIC: '^IXIC', DJI: '^DJI', RUT: '^RUT',
  VIX: '^VIX', SOX: '^SOX', N225: '^N225', SSE: '000001.SS',
};

export function quoteSymbol(value: string): string {
  const symbol = value.trim().toUpperCase();
  return INDEX_ALIASES[symbol] ?? symbol;
}

/* 大盘强弱页的时段、形态、信号和宏观都只算美股，指数概览里其他市场的指数要另列一组，
   解读里的涨跌个数也不能把它们算进去。列的是认得的美股指数，没列到的一律算其他市场：
   宁可把一个新加的美股指数放错组，也不让海外指数混进美股的涨跌统计。 */
const US_INDEX_SYMBOLS = new Set(['^GSPC', '^IXIC', '^NDX', '^DJI', '^RUT', '^VIX', '^SOX']);

export function isUsIndexSymbol(value: string): boolean {
  return US_INDEX_SYMBOLS.has(quoteSymbol(value));
}

/** 公司新闻与股票雷达不接受指数；行情、日线和技术信号仍使用原指数代码。 */
export function isIndexSymbol(value: string): boolean {
  const symbol = quoteSymbol(value);
  return symbol.startsWith('^') || symbol === '000001.SS';
}
