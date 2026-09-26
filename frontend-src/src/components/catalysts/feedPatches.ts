/**
 * 新闻抽屉回写列表的补丁。
 *
 * 补丁只是「列表下一次从服务端确认之前」的桥：列表快照在补丁之后确认过，就以快照为准。
 * 旧补丁若在切换筛选、打开别的新闻时重新套上，会把定时批次已分析完的条目退回「未分析」。
 */
import type { CatalystNewsItem } from './api';
import type { FeedSnapshot } from './feedSnapshot';

export interface NewsPatch {
  item: CatalystNewsItem;
  /** 抽屉拿到这份条目的时刻；与快照 validatedAt 同为 Date.now() 口径。 */
  at: number;
}

export type NewsPatches = Readonly<Record<string, NewsPatch>>;

/* 抽屉一次只看一条新闻，只需留最近若干条，集合不无限增长。 */
export const MAX_NEWS_PATCHES = 24;

export function addNewsPatch(previous: NewsPatches, item: CatalystNewsItem, at: number): NewsPatches {
  const next: Record<string, NewsPatch> = { ...previous, [item.newsId]: { item, at } };
  const ids = Object.keys(next);
  if (ids.length <= MAX_NEWS_PATCHES) return next;
  ids.sort((a, b) => next[a].at - next[b].at);
  for (const id of ids.slice(0, ids.length - MAX_NEWS_PATCHES)) delete next[id];
  return next;
}

/** 只套用比快照新的补丁；没有变化时原样返回同一个对象。 */
export function applyNewsPatches<T extends FeedSnapshot<CatalystNewsItem>>(
  snapshot: T,
  patches: NewsPatches,
  validatedAt: number,
): T {
  let changed = false;
  const items = snapshot.items.map((row) => {
    const patch = patches[row.newsId];
    if (!patch || patch.at <= validatedAt || patch.item === row) return row;
    changed = true;
    return patch.item;
  });
  return changed ? { ...snapshot, items } : snapshot;
}
