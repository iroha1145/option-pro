/** DataTable：发丝线行、r-lg 容器、表头 Eyebrow 化、行 hover paper-2 底、可排序 */
import { useMemo, useState, type KeyboardEvent as ReactKeyboardEvent, type ReactNode } from 'react';
import { motion } from 'framer-motion';
import { cn } from '@/lib/utils';
import Icon from '@/components/icons';

export interface Column<T> {
  key: string;
  /** 列头内容；th 内层是 inline-flex gap-1，可携带 InfoHint 等行内小件 */
  title: ReactNode;
  align?: 'left' | 'right' | 'center';
  width?: string;
  sortable?: boolean;
  sortValue?: (row: T) => number | string;
  render: (row: T, index: number) => ReactNode;
  className?: string;
}

export interface SortState {
  key: string;
  desc: boolean;
}

interface DataTableProps<T> {
  columns: Column<T>[];
  rows: T[];
  rowKey: (row: T) => string;
  onRowClick?: (row: T) => void;
  rowHeight?: 56 | 44;
  defaultSort?: SortState | null;
  /** 受控排序（与工具行下拉联动时用） */
  sort?: SortState | null;
  onSortChange?: (s: SortState | null) => void;
  className?: string;
  rowClassName?: (row: T) => string;
}

export default function DataTable<T>({
  columns,
  rows,
  rowKey,
  onRowClick,
  rowHeight = 56,
  defaultSort = null,
  sort: sortProp,
  onSortChange,
  className,
  rowClassName,
}: DataTableProps<T>) {
  const [innerSort, setInnerSort] = useState(defaultSort);
  const sort = sortProp !== undefined ? sortProp : innerSort;
  const setSort = (s: SortState | null) => {
    if (sortProp !== undefined) onSortChange?.(s);
    else setInnerSort(s);
  };

  const sorted = useMemo(() => {
    if (!sort) return rows;
    const col = columns.find((c) => c.key === sort.key);
    if (!col?.sortValue) return rows;
    const dir = sort.desc ? -1 : 1;
    /* 缺失值（null/NaN）恒沉底、与方向无关（watchlistSort 同语义）：把「无数据」
       当 0 分会让它随排序方向在头尾跳动，被读成极值。 */
    const missing = (v: unknown) =>
      v === null || v === undefined || (typeof v === 'number' && !Number.isFinite(v));
    return [...rows].sort((a, b) => {
      const va = col.sortValue!(a);
      const vb = col.sortValue!(b);
      const ma = missing(va);
      const mb = missing(vb);
      if (ma || mb) return ma && mb ? 0 : ma ? 1 : -1;
      if (typeof va === 'string' || typeof vb === 'string') return String(va).localeCompare(String(vb)) * dir;
      return ((va as number) - (vb as number)) * dir;
    });
  }, [rows, sort, columns]);

  const toggleSort = (key: string) => {
    setSort(sort?.key === key ? { key, desc: !sort.desc } : { key, desc: true });
  };

  return (
    <div className={cn('card-surface overflow-x-auto overscroll-x-contain', className)}>
      <table className="w-full border-collapse">
        <thead>
          <tr className="bg-card-warm">
            {columns.map((c) => (
              <th
                key={c.key}
                style={c.width ? { width: c.width } : undefined}
                className={cn(
                  'border-b border-line px-4 py-2.5 text-eyebrow font-sans uppercase tracking-[0.14em] text-ink-400',
                  c.align === 'right' ? 'text-right' : c.align === 'center' ? 'text-center' : 'text-left',
                  c.sortable && 'select-none',
                )}
                aria-sort={sort?.key === c.key ? (sort.desc ? 'descending' : 'ascending') : undefined}
              >
                {/* 排序必须是真正的按钮（审计 P3-1）：旧实现把 onClick 绑在 <th> 上，
                    既不可聚焦也没有键盘事件，纯鼠标操作。 */}
                {c.sortable ? (
                  <button
                    type="button"
                    onClick={() => toggleSort(c.key)}
                    className="inline-flex items-center gap-1 rounded-xs transition-colors duration-fast hover:text-ink-600 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500/30"
                  >
                    {c.title}
                    <span className={cn('inline-flex transition-transform duration-200', sort?.key === c.key && !sort.desc && 'rotate-180', sort?.key !== c.key && 'opacity-30')}>
                      <Icon name="chevron-down" size={11} />
                    </span>
                  </button>
                ) : (
                  <span className="inline-flex items-center gap-1">{c.title}</span>
                )}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {sorted.map((row, i) => {
            const key = rowKey(row);
            return (
              <motion.tr
                key={key}
                layout="position"
                transition={{ duration: 0.32, ease: [0.16, 1, 0.3, 1] }}
                onClick={onRowClick ? () => onRowClick(row) : undefined}
                /* 可点击行同时可聚焦、可回车/空格触发（审计 P3-1）。 */
                {...(onRowClick
                  ? {
                      tabIndex: 0,
                      role: 'button' as const,
                      onKeyDown: (event: ReactKeyboardEvent<HTMLTableRowElement>) => {
                        /* 只处理落在行本体上的按键：行内真按钮（如「从自选移除」）
                           的 Enter 激活是 keydown 的默认动作，容器 preventDefault
                           会吃掉它，让键盘用户执行到相反的动作（打开详情）。 */
                        if (event.target !== event.currentTarget) return;
                        if (event.key === 'Enter' || event.key === ' ') {
                          event.preventDefault();
                          onRowClick(row);
                        }
                      },
                    }
                  : {})}
                className={cn(
                  'group border-b border-line last:border-0 transition-colors duration-fast',
                  onRowClick && 'cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-brand-500/30',
                  'hover:bg-paper-2',
                  rowClassName?.(row),
                )}
                style={{ height: rowHeight }}
              >
                {columns.map((c) => (
                  <td
                    key={c.key}
                    className={cn(
                      'px-4 py-2 text-body-s text-ink-600',
                      c.align === 'right' ? 'text-right' : c.align === 'center' ? 'text-center' : 'text-left',
                      c.className,
                    )}
                  >
                    {c.render(row, i)}
                  </td>
                ))}
              </motion.tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
