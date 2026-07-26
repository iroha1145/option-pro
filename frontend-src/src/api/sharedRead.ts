/**
 * 全局只读端点的短窗口共享（按路径）。
 *
 * 首屏上有多个互不知情的组件在拉同一个接口。第一版把共享做在**映射结果**上，
 * 只覆盖得了同一个模块里的调用；实测 /market 页仍有两次 /market/status 和两次
 * /strength/market —— 因为 components/market/api.ts 用自己的 mapper 拉同一个
 * 端点，走的是另一条代码路径。
 *
 * 所以共享必须落在**原始响应**这一层：一次网络请求，各调用方各自映射成自己
 * 需要的形状。
 *
 * **只能用于对所有调用方返回同一份全局数据的只读端点。** 按用户、按参数变化的
 * 接口一律不能进来 —— 那会把一个人的数据发给另一个人。窗口刻意很短：它替代不了
 * 轮询，只压掉同一时刻的重复；各组件自己的轮询周期完全不变。
 *
 * 共享出去的对象被视为**只读**：多个 mapper 拿到的是同一个引用，谁改动谁就污染
 * 了别人。现有 mapper 都只读字段并另建对象。
 */
import { get } from './client';

const SHARE_WINDOW_MS = 2_000;

interface Entry {
  inFlight: Promise<unknown> | null;
  value: { at: number; value: unknown } | null;
}

const entries = new Map<string, Entry>();

/** 允许共享的路径白名单：全局、只读、与调用者身份无关。 */
const SHAREABLE = new Set(['/market/status', '/market/indices', '/strength/market']);

export function sharedGlobalGet<T>(path: string): Promise<T> {
  if (!SHAREABLE.has(path)) {
    // 没在白名单里就照常直发，避免有人顺手把按用户变化的接口塞进来。
    return get<T>(path);
  }
  let entry = entries.get(path);
  if (!entry) {
    entry = { inFlight: null, value: null };
    entries.set(path, entry);
  }
  const now = Date.now();
  if (entry.value && now - entry.value.at < SHARE_WINDOW_MS) {
    return Promise.resolve(entry.value.value as T);
  }
  if (entry.inFlight) return entry.inFlight as Promise<T>;
  const request = get<T>(path)
    .then((value) => {
      entry.value = { at: Date.now(), value };
      return value;
    })
    .finally(() => {
      if (entry.inFlight === request) entry.inFlight = null;
    });
  entry.inFlight = request;
  return request;
}

/** 测试用复位；生产依赖上面的有界过期。 */
export function resetSharedReads(): void {
  entries.clear();
}
