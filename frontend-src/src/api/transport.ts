/** Non-streaming JSON transport. The deadline includes response BODY download.
 * Keep the original Response (URL, headers, status, clone behaviour): drain a
 * clone under the deadline, so the original's queued body is ready for callers.
 * An upper bound prevents a malformed upstream from buffering forever.
 */
export class TransportTimeoutError extends Error {
  constructor() { super('Response deadline exceeded'); this.name = 'TransportTimeoutError'; }
}
export class ResponseLimitError extends Error {
  constructor() { super('Response exceeded byte limit'); this.name = 'ResponseLimitError'; }
}
export const MAX_RESPONSE_BYTES = 32 * 1024 * 1024;

export function apiHeaders(input: HeadersInit | undefined, write: boolean): Headers {
  const headers = new Headers(input);
  if (!headers.has('Content-Type')) headers.set('Content-Type', 'application/json');
  if (write) headers.set('X-Optix-Action', '1');
  return headers;
}

/** Both Retry-After forms: delta seconds and HTTP-date. Invalid is NOT zero. */
export function parseRetryAfter(value: unknown, now = Date.now()): number | undefined {
  if (typeof value === 'number') return Number.isFinite(value) && value >= 0 ? value : undefined;
  if (typeof value !== 'string' || !value.trim()) return undefined;
  const text = value.trim();
  if (/^\d+(?:\.\d+)?$/.test(text)) {
    const seconds = Number(text);
    return Number.isFinite(seconds) ? seconds : undefined;
  }
  // Date.parse accepts surprising inputs such as "-1" or "1e3". Only attempt
  // a textual HTTP date, not arbitrary numeric strings from an upstream.
  if (!/[a-z]{3}/i.test(text) || !/GMT$/i.test(text)) return undefined;
  const stamp = Date.parse(text);
  return Number.isFinite(stamp) && Number.isFinite(now) ? Math.max(0, Math.ceil((stamp - now) / 1000)) : undefined;
}

export async function fetchBuffered(
  input: RequestInfo | URL,
  init: RequestInit,
  timeoutMs: number,
  limit = MAX_RESPONSE_BYTES,
): Promise<Response> {
  if (!Number.isFinite(timeoutMs) || timeoutMs < 0 || !Number.isFinite(limit) || limit <= 0) {
    throw new RangeError('Invalid transport budget');
  }
  const controller = new AbortController();
  const external = init.signal;
  const abortExternal = () => controller.abort(external?.reason);
  let reader: ReadableStreamDefaultReader<Uint8Array> | undefined;
  let original: Response | undefined;
  let timer: ReturnType<typeof setTimeout> | undefined;
  let rejectAbort: (reason: unknown) => void = () => {};
  const aborted = new Promise<never>((_, reject) => { rejectAbort = reject; });
  const onAbort = () => rejectAbort(controller.signal.reason ?? new DOMException('Aborted', 'AbortError'));
  controller.signal.addEventListener('abort', onAbort, { once: true });
  if (external?.aborted) abortExternal();
  else external?.addEventListener('abort', abortExternal, { once: true });
  if (timeoutMs > 0) timer = setTimeout(() => controller.abort(new TransportTimeoutError()), timeoutMs);

  const work = async () => {
    controller.signal.throwIfAborted();
    original = await fetch(input, { ...init, signal: controller.signal });
    if (controller.signal.aborted) {
      // Also discard a late response from a fetch adapter that ignored abort.
      void original.body?.cancel(controller.signal.reason).catch(() => {});
      controller.signal.throwIfAborted();
    }
    if (!original.body) return original;
    // A response clone tees the stream; never leave the original branch
    // buffering an unbounded payload while waiting for the second branch.
    reader = original.clone().body!.getReader();
    let bytes = 0;
    while (true) {
      controller.signal.throwIfAborted();
      const next = await reader.read();
      if (next.done) break;
      bytes += next.value.byteLength;
      if (bytes > limit) throw new ResponseLimitError();
    }
    return original;
  };
  try {
    return await Promise.race([work(), aborted]);
  } catch (error) {
    // Cancellation of one tee alone can wait for the other branch. Do not
    // await these cancellations; abort the network and cancel BOTH branches.
    controller.abort(error);
    void reader?.cancel(error).catch(() => {});
    void original?.body?.cancel(error).catch(() => {});
    throw error;
  } finally {
    if (timer !== undefined) clearTimeout(timer);
    external?.removeEventListener('abort', abortExternal);
    controller.signal.removeEventListener('abort', onAbort);
    // The reader can still have a pending read on an aborted synthetic stream;
    // releaseLock is intentionally not required for garbage collection here.
  }
}
