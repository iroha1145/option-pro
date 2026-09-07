/** Shared, bounded stale-while-revalidate cache. No React or network dependency. */
export interface ResourceSnapshot<T> {
  data: T | null;
  error: unknown;
  refreshing: boolean;
  /** Last successful network validation, never the time a cached value was read. */
  validatedAt: number;
  restored: boolean;
}
export interface StoredResource {
  key: string;
  data: unknown;
  validatedAt: number;
  expiresAt: number;
}
export interface ResourcePersistence {
  read(key: string): Promise<StoredResource | null>;
  write(record: StoredResource): Promise<void>;
  remove(key: string): Promise<void>;
}
export interface ResourcePolicy<T> {
  freshMs: number | ((data: T | null) => number);
  retainMs: number;
  validate?: (value: unknown) => value is T;
}
interface Entry<T> {
  key: string;
  snapshot: ResourceSnapshot<T>;
  listeners: Set<() => void>;
  load?: (previous: T | null) => Promise<T>;
  policy: ResourcePolicy<T>;
  generation: number;
  pending: Promise<void> | null;
  hydrated: boolean;
  stale: boolean;
  lastUsed: number;
  retryAt: number;
  failures: number;
}
const empty = <T>(): ResourceSnapshot<T> => ({
  data: null, error: null, refreshing: false, validatedAt: 0, restored: false,
});

export class ResourceCache {
  private entries = new Map<string, Entry<unknown>>();
  constructor(
    private persistence?: ResourcePersistence,
    private now: () => number = Date.now,
    private maxEntries = 48,
  ) {}

  private entry<T>(key: string, policy: ResourcePolicy<T>): Entry<T> {
    let entry = this.entries.get(key) as Entry<T> | undefined;
    if (!entry) {
      entry = { key, policy, snapshot: empty<T>(), listeners: new Set(),
        generation: 0, pending: null, hydrated: false, stale: false,
        lastUsed: this.now(), retryAt: 0, failures: 0 };
      this.entries.set(key, entry as Entry<unknown>);
    }
    entry.policy = policy;
    entry.lastUsed = this.now();
    return entry;
  }

  snapshot<T>(key: string, policy: ResourcePolicy<T>): ResourceSnapshot<T> {
    const entry = this.entry(key, policy);
    // Hard-expired data must not flash for one frame on a later remount.
    // Keep the resulting empty snapshot stable for useSyncExternalStore.
    if (entry.snapshot.data !== null && this.now() - entry.snapshot.validatedAt >= policy.retainMs) {
      entry.snapshot = empty<T>();
      entry.stale = true;
    }
    return entry.snapshot;
  }

  subscribe<T>(key: string, policy: ResourcePolicy<T>, listener: () => void): () => void {
    const entry = this.entry(key, policy);
    entry.listeners.add(listener);
    this.prune();
    return () => {
      entry.listeners.delete(listener);
      entry.lastUsed = this.now();
      this.prune();
    };
  }

  private publish<T>(entry: Entry<T>, snapshot: ResourceSnapshot<T>): void {
    entry.snapshot = snapshot;
    for (const listener of [...entry.listeners]) listener();
  }

  private valid<T>(entry: Entry<T>, generation: number): boolean {
    return entry.generation === generation && this.entries.get(entry.key) === entry;
  }

  async ensure<T>(
    key: string,
    policy: ResourcePolicy<T>,
    load?: (previous: T | null) => Promise<T>,
    force = false,
  ): Promise<void> {
    const entry = this.entry(key, policy);
    if (load) entry.load = load;
    if (!entry.load) return;
    if (force) {
      entry.generation += 1;
      entry.pending = null;
      entry.stale = true;
      entry.retryAt = 0;
    }
    if (entry.pending) return entry.pending;
    const generation = entry.generation;
    const task = async () => {
      if (!entry.hydrated) {
        entry.hydrated = true;
        // Disk must not indefinitely hold the first network request (private mode,
        // blocked upgrade, corrupt browser storage). Late restore never writes UI.
        if (this.persistence) {
          let timer: ReturnType<typeof setTimeout> | undefined;
          const record = await Promise.race([
            this.persistence.read(key).catch(() => null),
            new Promise<null>((resolve) => { timer = setTimeout(() => resolve(null), 700); }),
          ]).finally(() => { if (timer) clearTimeout(timer); });
          if (!this.valid(entry, generation)) return;
          const age = record ? this.now() - record.validatedAt : Infinity;
          if (record && record.key === key && record.expiresAt > this.now() && record.data !== null
              && (!policy.validate || policy.validate(record.data))
              && age >= 0 && age <= policy.retainMs && entry.snapshot.data === null) {
            this.publish(entry, { data: record.data as T, error: null,
              refreshing: false, validatedAt: record.validatedAt, restored: true });
            // A disk record is a fallback, not proof that the current server agrees.
            entry.stale = true;
          }
        }
      }
      if (!this.valid(entry, generation)) return;
      if (entry.snapshot.data !== null && this.now() - entry.snapshot.validatedAt >= policy.retainMs) {
        this.publish(entry, empty<T>());
        entry.stale = true;
      }
      const freshMs = typeof policy.freshMs === 'function'
        ? policy.freshMs(entry.snapshot.data) : policy.freshMs;
      if (!entry.stale && entry.snapshot.data !== null
          && this.now() - entry.snapshot.validatedAt < freshMs) return;
      if (this.now() < entry.retryAt) return;
      this.publish(entry, { ...entry.snapshot, refreshing: true });
      try {
        const data = await entry.load!(entry.snapshot.data);
        if (!this.valid(entry, generation)) return;
        const at = this.now();
        // Preserve references for unchanged snapshots; stable row keys preserve DOM.
        const unchanged = entry.snapshot.data !== null
          && JSON.stringify(entry.snapshot.data) === JSON.stringify(data);
        entry.stale = false;
        entry.failures = 0;
        entry.retryAt = 0;
        this.publish(entry, { data: unchanged ? entry.snapshot.data : data,
          error: null, refreshing: false, validatedAt: at, restored: false });
        void this.persistence?.write({ key, data: entry.snapshot.data,
          validatedAt: at, expiresAt: at + policy.retainMs }).catch(() => undefined);
      } catch (error) {
        if (!this.valid(entry, generation)) return;
        entry.failures += 1;
        const retryAfter = (error as { retryAfter?: unknown } | null)?.retryAfter;
        const serverDelay = typeof retryAfter === 'number' && Number.isFinite(retryAfter)
          ? Math.max(0, retryAfter) * 1000 : 0;
        entry.retryAt = this.now() + Math.max(serverDelay, Math.min(300_000, 15_000 * 2 ** Math.min(5, entry.failures - 1)));
        this.publish(entry, { ...entry.snapshot, error, refreshing: false });
      }
    };
    // Assign before task starts: StrictMode and concurrent subscribers share it.
    const pending = Promise.resolve().then(task).finally(() => {
      if (entry.pending === pending) entry.pending = null;
      this.prune();
    });
    entry.pending = pending;
    return pending;
  }

  /** Invalidate old responses too; an in-flight pre-write response cannot win. */
  invalidate(): void {
    for (const entry of this.entries.values()) {
      entry.generation += 1;
      entry.pending = null;
      entry.stale = true;
      entry.retryAt = 0;
      if (entry.snapshot.refreshing) this.publish(entry, { ...entry.snapshot, refreshing: false });
    }
  }

  tick(): void {
    for (const entry of this.entries.values()) {
      if (entry.listeners.size && entry.load) void this.ensure(entry.key, entry.policy);
    }
    this.prune();
  }

  /** Conditional UI mutation: rejects stale pagination responses after a refresh. */
  update<T>(key: string, policy: ResourcePolicy<T>,
    updater: (data: T | null) => T | null, expected?: T | null): boolean {
    const entry = this.entry(key, policy);
    if (expected !== undefined && (entry.snapshot.data !== expected || entry.stale || entry.snapshot.refreshing)) return false;
    const data = updater(entry.snapshot.data);
    if (data === null || data === entry.snapshot.data) return false;
    // A manual mutation also supersedes an older background request.
    entry.generation += 1;
    entry.pending = null;
    this.publish(entry, { ...entry.snapshot, data, refreshing: false });
    // Do not falsify last network validation on an optimistic or paginated update.
    if (entry.snapshot.validatedAt > 0) void this.persistence?.write({ key, data,
      validatedAt: entry.snapshot.validatedAt,
      expiresAt: entry.snapshot.validatedAt + policy.retainMs }).catch(() => undefined);
    return true;
  }

  private prune(): void {
    const inactive = [...this.entries.values()]
      .filter((entry) => !entry.listeners.size && !entry.pending)
      .sort((a, b) => a.lastUsed - b.lastUsed);
    for (const entry of inactive) {
      const expired = this.now() - entry.lastUsed > entry.policy.retainMs;
      if (this.entries.size <= this.maxEntries && !expired) continue;
      entry.generation += 1;
      this.entries.delete(entry.key);
      // Memory eviction is not a reason to lose the disk fallback.
      if (expired) void this.persistence?.remove(entry.key).catch(() => undefined);
    }
  }
}
