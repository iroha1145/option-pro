/**
 * HistoryRail · 当日信号区右栏「历史事件回溯」压缩面板（V6）
 * 标题行「历史事件回溯 · 共 N 条」+ 副标（按时间倒序 / 筛选计数 / 过期标记）
 * 紧凑行（~52px，1px line 发丝线分隔，非卡片堆叠）：
 *   左 时间（HH:MM Mono）+ ticker/名称（一行）· 形态 + lifecycle 语义色小点+文字（二行小字）
 *   右 现价 Mono + 状态色点；行 hover paper-2；点击开 EventDetail 模态
 * 日期分组小头（Serif 13px + 计数）· 游标分页「加载更多 · 剩 N 条」（复用原时间线分页逻辑）
 * 面板与大卡等高对齐（~560px 上限 + 内部细滚动条）
 */
import { useMemo, useRef, useState } from 'react';
import { motion } from 'framer-motion';
import type { ApiError } from '@/api/client';
import { cn } from '@/lib/utils';
import { fmtPrice } from '@/lib/format';
import EmptyState from '@/components/shared/EmptyState';
import { SkeletonRows } from '@/components/shared/Skeleton';
import { LIFECYCLE_CN, LIFECYCLE_TONE, SETUP_CN } from './types';
import type { BreakoutEventFull, LifecycleTone } from './types';
import { getLocale, t } from '../../i18n/core.ts';

const PAGE = 12;
const EASE_PAPER = [0.16, 1, 0.3, 1] as [number, number, number, number];

/** lifecycle 语义色小点（不用大色块） */
const TONE_DOT: Record<LifecycleTone, string> = {
  brand: 'bg-brand-600',
  up: 'bg-up-600',
  down: 'bg-down-600',
  ink: 'bg-ink-400',
};
const TONE_TEXT: Record<LifecycleTone, string> = {
  brand: 'text-brand-600',
  up: 'text-up-700',
  down: 'text-down-700',
  ink: 'text-ink-500',
};

function hhmm(iso: string | null | undefined): string {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '—';
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
}

function dayLabel(key: string): string {
  const d = new Date(`${key}T12:00:00`);
  const weekZh = [t('周日'), t('周一'), t('周二'), t('周三'), t('周四'), t('周五'), t('周六')][d.getDay()];
  const week = t(weekZh);
  const locale = getLocale();
  // en：紧凑数字日期 + 缩写星期（"7/26 Sun"）；ja：原生日期写法（"7月26日（日）"）；
  // zh：保留原版空格分词，三种语言各自的自然写法互不通用，不能共享一个模板。
  if (locale === 'en') return `${d.getMonth() + 1}/${d.getDate()} ${week}`;
  if (locale === 'ja') return `${d.getMonth() + 1}月${d.getDate()}日（${week}）`;
  return `${d.getMonth() + 1} 月 ${d.getDate()} 日 ${week}`;
}

interface HistoryRailProps {
  events: BreakoutEventFull[];
  /** 已加载条数（筛选前）。 */
  loadedCount: number;
  /**
   * 服务端总数；契约不返回时为 null（审计 P2-19）。
   * 旧实现在缺失时用当前数组长度或 `page*pageSize+1` 补位，等于展示一个拼出来的数字。
   */
  total: number | null;
  /** 服务端还有更多（next_cursor 非空）；与组件内的「本地展开」是两回事。 */
  serverHasMore: boolean;
  loadingServerMore: boolean;
  serverMoreError: ApiError | null;
  onFetchMore: () => void;
  /** 轮询过期（保留旧数据时出错） */
  stale: boolean;
  loading: boolean;
  error: ApiError | null;
  onRetry: () => void;
  onOpenDetail: (ev: BreakoutEventFull) => void;
}

export default function HistoryRail({
  events,
  loadedCount,
  total,
  serverHasMore,
  loadingServerMore,
  serverMoreError,
  onFetchMore,
  stale,
  loading,
  error,
  onRetry,
  onOpenDetail,
}: HistoryRailProps) {
  const [visible, setVisible] = useState(PAGE);
  const moreTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);

  /* 上游过滤结果变化时重置分页（渲染期派生，避免 effect 级联） */
  const [prevEvents, setPrevEvents] = useState(events);
  if (prevEvents !== events) {
    setPrevEvents(events);
    setVisible(PAGE);
  }

  const shown = events.slice(0, visible);
  const groups = useMemo(() => {
    const map = new Map<string, BreakoutEventFull[]>();
    shown.forEach((e) => {
      const key = new Date(e.event_at).toLocaleDateString('en-CA');
      const arr = map.get(key) ?? [];
      arr.push(e);
      map.set(key, arr);
    });
    return [...map.entries()].sort((a, b) => (a[0] < b[0] ? 1 : -1));
  }, [shown]);

  const hasMore = events.length > visible;
  const onLoadMore = () => {
    setLoadingMore(true);
    moreTimer.current = setTimeout(() => {
      setVisible((v) => v + PAGE);
      setLoadingMore(false);
    }, 320);
  };

  let rowIndex = 0;

  return (
    <section aria-label={t("历史事件回溯")} className="card-surface flex max-h-[560px] flex-col overflow-hidden">
      {/* 标题行 + 副标 */}
      <div className="shrink-0 border-b border-line px-4 pb-2.5 pt-3.5">
        <p className="flex items-baseline justify-between gap-2">
          <span className="text-body-s font-semibold text-ink-900">
            {t('历史事件回溯 ·')}{' '}
            <span className="font-mono tnum">
              {total !== null ? `共 ${total} 条` : `已加载 ${loadedCount} 条${serverHasMore ? '+' : ''}`}
            </span>
          </span>
          {stale && (
            <span className="rounded-xs border border-warn-600/40 bg-warn-50 px-1.5 py-px text-micro text-warn-600">{t('已过期')}</span>
          )}
        </p>
        <p className="mt-1 text-micro text-ink-400">
          {t('按时间倒序')}
          {events.length !== loadedCount && (
            <span className="font-mono tnum"> {t('· 筛选出')} {events.length} {t('条')}</span>
          )}
          <span className="mx-1 text-ink-300" aria-hidden="true">·</span>
          {t('点击行打开事件详情')}
        </p>
      </div>

      {/* 列表区：内部滚动（自定义细滚动条） */}
      <div className="bk-rail-scroll min-h-0 flex-1 overflow-y-auto">
        {loading ? (
          <SkeletonRows rows={6} />
        ) : error ? (
          <EmptyState
            variant="error"
            icon="doc-quote"
            title={error.code === 503 ? t('事件数据暂不可用') : t('事件加载失败')}
            description={error.code === 503 ? t('稍后刷新再试') : error.message}
            action={
              <button
                onClick={onRetry}
                className="flex items-center gap-2 rounded-md bg-brand-600 px-4 py-2 text-caption font-medium text-white transition-[filter] hover:brightness-105"
              >
                {t('重试')}
              </button>
            }
          />
        ) : events.length === 0 ? (
          <EmptyState
            image="/empty-radar.svg"
            title={t("暂无匹配的历史事件")}
            description={t("放宽页头筛选条件，或等雷达下一轮扫描把事件沉淀到这里")}
            action={
              <button
                onClick={onRetry}
                className="flex items-center gap-2 rounded-md bg-brand-600 px-4 py-2 text-caption font-medium text-white transition-[filter] hover:brightness-105"
              >
                {t('重新加载')}
              </button>
            }
          />
        ) : (
          <div>
            {groups.map(([day, items]) => (
              <div key={day}>
                {/* 日期分组小头（Serif 13px + 计数） */}
                <div className="flex items-baseline justify-between border-b border-line bg-card-warm px-3 py-1.5">
                  <p className="font-display text-[13px] leading-[18px] text-ink-800">{dayLabel(day)}</p>
                  <span className="font-mono text-micro text-ink-400 tnum">{items.length} {t('条')}</span>
                </div>
                <ul className="divide-y divide-line">
                  {items.map((e) => {
                    const i = rowIndex++;
                    const tone = LIFECYCLE_TONE[e.lifecycle_state] ?? 'ink';
                    return (
                      <motion.li
                        key={e.event_id}
                        /* 繁忙长列表：transform 字符串写法；stagger ≤30ms 且仅第一页入场，加载更多直接呈现 */
                        initial={i < PAGE ? { opacity: 0, transform: 'translateY(8px)' } : false}
                        animate={{ opacity: 1, transform: 'translateY(0px)' }}
                        transition={{ duration: 0.36, ease: EASE_PAPER, delay: i < PAGE ? Math.min(i * 0.03, 0.36) : 0 }}
                      >
                        <div
                          role="button"
                          tabIndex={0}
                          onClick={() => onOpenDetail(e)}
                          onKeyDown={(ev) => {
                            if (ev.key === 'Enter' || ev.key === ' ') {
                              ev.preventDefault();
                              onOpenDetail(e);
                            }
                          }}
                          aria-label={`${e.ticker} ${SETUP_CN[e.setup_type] ?? e.setup_type ?? ''}，${LIFECYCLE_CN[e.lifecycle_state] ?? e.lifecycle_state ?? ''}，打开事件详情`}
                          className="flex min-h-[52px] cursor-pointer items-center gap-2.5 px-3 py-1.5 transition-colors duration-fast hover:bg-paper-2"
                        >
                          {/* 时间 */}
                          <span className="w-10 shrink-0 font-mono text-caption text-ink-400 tnum">{hhmm(e.event_at)}</span>
                          {/* ticker/名称 + 形态·状态 */}
                          <span className="min-w-0 flex-1">
                            <span className="flex items-baseline gap-1.5">
                              <span className="shrink-0 font-mono text-body-s font-semibold text-ink-800">{e.ticker}</span>
                              <span className="truncate text-micro text-ink-400">{e.name}</span>
                            </span>
                            <span className="mt-0.5 flex items-center gap-1.5 text-micro leading-[14px]">
                              <span className="truncate text-ink-400">{SETUP_CN[e.setup_type] ?? e.setup_type ?? '—'}</span>
                              <span className={cn('inline-flex shrink-0 items-center gap-1 font-medium', TONE_TEXT[tone])}>
                                <span className={cn('size-1.5 rounded-full', TONE_DOT[tone])} aria-hidden="true" />
                                {LIFECYCLE_CN[e.lifecycle_state] ?? e.lifecycle_state ?? '—'}
                              </span>
                            </span>
                          </span>
                          {/* 现价 + 状态色点 */}
                          <span className="flex shrink-0 items-center gap-1.5">
                            <span className="font-mono text-caption text-ink-800 tnum">
                              {typeof e.current_price === 'number' && Number.isFinite(e.current_price) ? fmtPrice(e.current_price) : '—'}
                            </span>
                            <span className={cn('size-1.5 rounded-full', TONE_DOT[tone])} aria-hidden="true" />
                          </span>
                        </div>
                      </motion.li>
                    );
                  })}
                </ul>
              </div>
            ))}

            {/* 两级「加载更多」：先展开已取回的，再向服务端续读（审计 P2-19）。
                旧实现只有本地展开，服务端还有多少条完全不可见。 */}
            <div className="flex flex-col items-center gap-1.5 border-t border-line px-3 py-2.5">
              {hasMore ? (
                <button
                  onClick={onLoadMore}
                  disabled={loadingMore}
                  className="flex items-center gap-2 rounded-md border border-line bg-card px-3 py-1.5 text-caption font-medium text-ink-600 transition-colors duration-fast hover:border-brand-400 hover:text-brand-600 disabled:opacity-60"
                >
                  {loadingMore && <span className="size-3.5 animate-spin rounded-full border-2 border-brand-100 border-t-brand-600" />}
                  {t('加载更多')}
                  <span className="font-mono text-micro text-ink-400 tnum">{t('剩')} {events.length - visible} {t('条')}</span>
                </button>
              ) : serverHasMore ? (
                <button
                  onClick={onFetchMore}
                  disabled={loadingServerMore}
                  className="flex items-center gap-2 rounded-md border border-line bg-card px-3 py-1.5 text-caption font-medium text-ink-600 transition-colors duration-fast hover:border-brand-400 hover:text-brand-600 disabled:opacity-60"
                >
                  {loadingServerMore && <span className="size-3.5 animate-spin rounded-full border-2 border-brand-100 border-t-brand-600" />}
                  {t('继续读取更早事件')}
                </button>
              ) : (
                <p className="font-mono text-micro text-ink-300 tnum">{t('已加载全部')} {events.length} {t('条')}</p>
              )}
              {serverMoreError && (
                <p className="text-micro text-down-700">
                  {t('加载更多失败：')}{serverMoreError.message}
                </p>
              )}
            </div>
          </div>
        )}
      </div>

      {/* 细滚动条（面板内局部，不触碰全局样式） */}
      <style>{`
        .bk-rail-scroll { scrollbar-width: thin; scrollbar-color: var(--line-strong) transparent; }
        .bk-rail-scroll::-webkit-scrollbar { width: 5px; }
        .bk-rail-scroll::-webkit-scrollbar-thumb { background: var(--line-strong); border-radius: 999px; }
        .bk-rail-scroll::-webkit-scrollbar-track { background: transparent; }
      `}</style>
    </section>
  );
}
