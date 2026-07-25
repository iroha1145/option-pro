/**
 * 「最近查看」本地存储（GPT-5.6-Pro 审计 P1-09）。
 *
 * 原本内联在 CommandPalette 里，且只做了 TypeScript 断言、没有任何运行时校验：
 * 合法 JSON 只要不是数组（`{}`、`null`、`"NVDA"`）随后的 .filter/.forEach 就会抛
 * 异常。命令面板始终挂载、且位于路由错误边界之外，因此这一条损坏的本地存储值
 * 足以让整站白屏。最小复现：
 *
 *   localStorage.setItem('optix:recent-tickers', '{}');
 *   location.reload();
 *
 * 拆成独立模块的另一个原因是它不该依赖 React 才能被验证 —— 校验逻辑要能直接测。
 */

export const RECENT_KEY = 'optix:recent-tickers';
export const RECENT_LIMIT = 5;
/** 代码只可能是字母、数字与 . - ^ 三个符号；长度封顶避免超大条目。 */
const TICKER_SHAPE = /^[A-Za-z0-9.^-]{1,12}$/;

function storage(): Storage | null {
  try {
    return typeof localStorage === 'undefined' ? null : localStorage;
  } catch {
    // 隐私模式等场景下访问 localStorage 本身就会抛。
    return null;
  }
}

/** 读取最近查看记录：校验数组、逐项校验形状、封顶条数；坏值就地清理。 */
export function readRecent(): string[] {
  const store = storage();
  if (!store) return [];
  try {
    const raw = store.getItem(RECENT_KEY);
    if (raw === null) return [];
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) {
      store.removeItem(RECENT_KEY);
      return [];
    }
    const clean = parsed.filter(
      (item): item is string => typeof item === 'string' && TICKER_SHAPE.test(item),
    );
    const bounded = clean.slice(0, RECENT_LIMIT);
    // 混入了非法项就顺手写回干净值，避免每次读取都重新过滤。
    if (clean.length !== parsed.length || bounded.length !== clean.length) {
      store.setItem(RECENT_KEY, JSON.stringify(bounded));
    }
    return bounded;
  } catch {
    try {
      store.removeItem(RECENT_KEY);
    } catch {
      /* 存储不可用时无从清理，返回空表即可 */
    }
    return [];
  }
}

export function pushRecent(ticker: string): void {
  const store = storage();
  if (!store || !TICKER_SHAPE.test(ticker)) return;
  try {
    const list = readRecent();
    const next = [ticker, ...list.filter((t) => t !== ticker)].slice(0, RECENT_LIMIT);
    store.setItem(RECENT_KEY, JSON.stringify(next));
  } catch {
    /* ignore */
  }
}
