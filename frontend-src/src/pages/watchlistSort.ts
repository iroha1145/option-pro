/**
 * 自选列表的唯一排序实现。
 *
 * 为什么要单独提出来：渐进挂载只渲染前 N 条，所以「先排序再切片」和
 * 「先切片再排序」得到的是**不同的股票**。表格自己还会再排一次，于是排序逻辑
 * 一旦有两份，就会出现「任意前 24 只里涨得最多的」冒充「涨得最多的 24 只」。
 *
 * 卡片、表格、渐进切片全部消费这一个函数的结果。表头能点的每一列都必须在这里
 * 有对应分支 —— 漏一列不会报错，只会静默按别的字段排序。
 */
import type { WatchlistItem } from '@/api/types';
import type { SortState } from '@/components/shared/DataTable';

/** 表头可排序的列；新增可排序列时必须同步这里，否则该列的全局排序是错的。 */
export type WatchlistSortKey = 'ticker' | 'price' | 'changePct' | 'strength';

const NUMERIC_ACCESSORS: Record<
  Exclude<WatchlistSortKey, 'ticker'>,
  (row: WatchlistItem) => number
> = {
  price: (row) => row.price,
  changePct: (row) => row.changePct,
  strength: (row) => row.strengthScore,
};

function comparable(value: number): number | null {
  return Number.isFinite(value) ? value : null;
}

/**
 * 按 sort 对完整列表排序；sort 为空时保持接口给的顺序。
 *
 * 缺失值一律排在末尾，且与升降序无关 —— 「没有数据」不是「最小的数据」，
 * 让它随排序方向在头尾之间跳动会让人以为那是极值。
 */
export function sortWatchlistItems(
  items: WatchlistItem[],
  sort: SortState | null | undefined,
): WatchlistItem[] {
  if (!sort) return items;
  const direction = sort.desc ? -1 : 1;
  const key = sort.key as WatchlistSortKey;

  if (key === 'ticker') {
    return [...items].sort(
      (left, right) => left.ticker.localeCompare(right.ticker) * direction,
    );
  }

  const accessor = NUMERIC_ACCESSORS[key as Exclude<WatchlistSortKey, 'ticker'>];
  if (!accessor) {
    // 未知的排序键：保持原顺序，而不是悄悄按涨跌幅排。
    // 旧实现把「除 ticker/strength 之外的一切」都当成 changePct，于是点「最新价」
    // 表头时，用于切片的全局排序其实是按涨跌幅排的。
    return items;
  }

  return [...items].sort((left, right) => {
    const a = comparable(accessor(left));
    const b = comparable(accessor(right));
    if (a === null && b === null) return left.ticker.localeCompare(right.ticker);
    if (a === null) return 1;
    if (b === null) return -1;
    return (a - b) * direction || left.ticker.localeCompare(right.ticker);
  });
}
