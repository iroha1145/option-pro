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
