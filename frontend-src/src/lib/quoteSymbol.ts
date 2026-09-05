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
