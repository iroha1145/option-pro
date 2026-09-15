type PreferenceWriter<T> = () => Promise<T>;

let writeTail: Promise<unknown> = Promise.resolve();

/** Serialize remote preference writes so the last queued patch is stored last. */
export function enqueuePreferenceWrite<T>(write: PreferenceWriter<T>): Promise<T> {
  const run = writeTail.then(write, write);
  writeTail = run.then(() => undefined, () => undefined);
  return run;
}

export function resetPreferenceWriteQueue(): void {
  writeTail = Promise.resolve();
}

/** Persist is best-effort. A failed PUT must not change the in-flight choice. */
export async function persistRemoteOrKeepLocal<T extends object>(
  local: T,
  write: PreferenceWriter<T>,
): Promise<T & { persisted?: boolean; syncError?: unknown }> {
  try {
    return await enqueuePreferenceWrite(write);
  } catch (error) {
    return { ...local, persisted: false, syncError: error };
  }
}
