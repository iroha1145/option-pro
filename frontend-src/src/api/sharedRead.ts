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

/**
 * 允许共享的路径白名单：全局、只读、**在同一身份下**返回同一份数据。
 *
 * `/strength/market` 并不是完全与身份无关：访客读的是落库的公开快照，owner 会
 * 实时算一遍。同一时刻的所有调用方身份相同，所以共享本身是对的；错的是跨越一次
 * 身份切换 —— 登录成功后的 2 秒内还可能复用访客那份。因此 dropSharedReads 必须
 * 挂在身份变更上（useAccess 的世代递增处），不能只靠这 2 秒自然过期。
 */
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

/**
 * 作废所有共享读。身份可能变了，这些响应就不再代表当前身份。
 *
 * 只 clear 就够：在途请求会为它的原调用方正常兑现（那次请求本来就是旧身份发的，
 * 给它旧数据是对的），但它写回的是已经从表里摘掉的那个 entry，不会污染新身份。
 */
export function dropSharedReads(): void {
  entries.clear();
}

/** 测试用复位；生产依赖上面的有界过期与 dropSharedReads。 */
export function resetSharedReads(): void {
  entries.clear();
}
