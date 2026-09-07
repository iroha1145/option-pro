import { catalystsContract, type CatalystNewsItem } from './api';
import type { CatalystFilters } from './filters';
import { toFeedQuery } from './filters';
import type { ResourcePolicy } from './resourceCache';
import { refreshFeedSnapshot, type FeedSnapshot } from './feedSnapshot';
import { useCatalystResource } from './useCatalystResource';

type Snapshot = FeedSnapshot<CatalystNewsItem>;
const POLICY: ResourcePolicy<Snapshot> = { freshMs: 120_000, retainMs: 2 * 24 * 60 * 60_000,
  validate: (value): value is Snapshot => {
    const x = value as Snapshot | null;
    return !!x && Array.isArray(x.items) && Number.isInteger(x.pages) && x.pages > 0 && Number.isFinite(x.total)
      && (x.nextCursor === null || typeof x.nextCursor === 'string')
      && x.items.every((item) => item && typeof item.newsId === 'string' && Array.isArray(item.sourceTickers));
  },
};
export function useFeedResource(filters: CatalystFilters) {
  const query = { ...toFeedQuery(filters), limit: 12 };
  return useCatalystResource(`feed:${JSON.stringify(query)}`, POLICY, async (previous) => {
    const result = await refreshFeedSnapshot((cursor) => catalystsContract.feed({ ...query, cursor }), previous);
    if (!result.items.length && !result.hiddenUnanalyzed) {
      try { result.hiddenUnanalyzed = (await catalystsContract.newsToday()).pending; } catch { /* feed remains valid */ }
    }
    return result;
  });
}
