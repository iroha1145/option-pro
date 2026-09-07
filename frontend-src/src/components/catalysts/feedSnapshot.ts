/** Pagination uses a fresh cursor chain after invalidation, never mixes cursors
 * from different feed snapshots. Removed / corrected / reclassified rows converge. */
export interface FeedPage<T> { items: T[]; nextCursor: string | null; total: number; hiddenUnanalyzed: number }
export interface FeedSnapshot<T> extends FeedPage<T> { pages: number }
export async function visibleFeedPage<T>(read: (cursor?: string) => Promise<FeedPage<T>>, cursor?: string): Promise<FeedPage<T>> {
  let result = await read(cursor);
  let hidden = result.hiddenUnanalyzed;
  const total = result.total;
  const seen = new Set<string>();
  for (let hops = 0; result.items.length === 0 && result.nextCursor && hops < 8; hops += 1) {
    if (seen.has(result.nextCursor)) break;
    seen.add(result.nextCursor);
    result = await read(result.nextCursor);
    hidden += result.hiddenUnanalyzed;
  }
  return { ...result, total, hiddenUnanalyzed: hidden };
}
export function appendFeedPage<T extends { newsId: string }>(previous: FeedSnapshot<T>, page: FeedPage<T>): FeedSnapshot<T> {
  const ids = new Set(previous.items.map((item) => item.newsId));
  return { ...previous, items: [...previous.items, ...page.items.filter((item) => {
    if (ids.has(item.newsId)) return false;
    ids.add(item.newsId); return true;
  })], nextCursor: page.nextCursor, hiddenUnanalyzed: previous.hiddenUnanalyzed + page.hiddenUnanalyzed, pages: previous.pages + 1 };
}
export async function refreshFeedSnapshot<T extends { newsId: string }>(read: (cursor?: string) => Promise<FeedPage<T>>, previous: FeedSnapshot<T> | null): Promise<FeedSnapshot<T>> {
  let snapshot: FeedSnapshot<T> = { ...await visibleFeedPage(read), pages: 1 };
  const pages = previous?.pages ?? 1;
  const seen = new Set<string>();
  while (snapshot.pages < pages && snapshot.nextCursor && !seen.has(snapshot.nextCursor)) {
    seen.add(snapshot.nextCursor);
    snapshot = appendFeedPage(snapshot, await visibleFeedPage(read, snapshot.nextCursor));
  }
  return snapshot;
}
