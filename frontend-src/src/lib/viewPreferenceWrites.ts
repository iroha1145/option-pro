type PreferenceWriter<T> = (signal?: AbortSignal) => Promise<T>;

export class PreferenceWriteCancelledError extends Error {
  constructor(message = 'preference write cancelled') {
    super(message);
    this.name = 'PreferenceWriteCancelledError';
  }
}

let writeGeneration = 0;
let boundPrincipal: string | undefined;
let writeTail: Promise<unknown> = Promise.resolve();
const inFlightControllers = new Set<AbortController>();

export function currentPreferenceWriteGeneration(): number {
  return writeGeneration;
}

export function bindPreferenceWritePrincipal(principal: string): void {
  boundPrincipal = principal;
}

/** Cancel queued writes. In-flight requests may abort; already-committed server writes cannot. */
export function invalidatePreferenceWriteQueue(): void {
  writeGeneration += 1;
  for (const controller of inFlightControllers) {
    controller.abort();
  }
  inFlightControllers.clear();
  writeTail = Promise.resolve();
}

export function resetPreferenceWriteQueue(): void {
  invalidatePreferenceWriteQueue();
  boundPrincipal = undefined;
}

function writeIsCurrent(principal: string | undefined, generation: number): boolean {
  if (generation !== writeGeneration) return false;
  if (principal !== undefined && boundPrincipal !== undefined && principal !== boundPrincipal) {
    return false;
  }
  return true;
}

/** Serialize remote preference writes so the last queued patch is stored last. */
export function enqueuePreferenceWrite<T>(
  write: PreferenceWriter<T>,
  bind?: { principal?: string | null; generation?: number },
): Promise<T> {
  const capturedPrincipal = bind?.principal === undefined ? undefined : (bind.principal ?? undefined);
  const capturedGeneration = bind?.generation ?? writeGeneration;
  const controller = new AbortController();
  inFlightControllers.add(controller);
  const dispatch = async () => {
    if (!writeIsCurrent(capturedPrincipal, capturedGeneration)) {
      throw new PreferenceWriteCancelledError();
    }
    return write(controller.signal);
  };
  const run = writeTail.then(dispatch, dispatch);
  writeTail = run.then(() => undefined, () => undefined);
  void run.finally(() => {
    inFlightControllers.delete(controller);
  }).catch(() => undefined);
  return run;
}

/** Persist is best-effort. A failed PUT must not change the in-flight choice. */
export async function persistRemoteOrKeepLocal<T extends object>(
  local: T,
  write: PreferenceWriter<T>,
  bind?: { principal?: string | null; generation?: number },
): Promise<T & { persisted?: boolean; syncError?: unknown }> {
  try {
    return await enqueuePreferenceWrite(write, bind);
  } catch (error) {
    return { ...local, persisted: false, syncError: error };
  }
}
