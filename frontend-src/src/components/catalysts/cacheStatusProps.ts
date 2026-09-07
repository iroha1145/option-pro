export function cacheStatusProps(q: {
  data: unknown;
  error: unknown;
  refreshing: boolean;
  restored: boolean;
  validatedAt: number;
  refresh: () => void;
}) {
  return {
    data: q.data,
    error: q.error,
    refreshing: q.refreshing,
    restored: q.restored,
    validatedAt: q.validatedAt,
    refresh: q.refresh,
  };
}
