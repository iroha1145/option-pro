/**
 * 统一的远端数据四态判定（GPT-5.6-Pro 审计「共同根因 1」）。
 *
 * 各页面此前各自手写 loading / error / data / empty 的组合，于是同一种请求失败
 * 在不同页面被解释成整页错误、空数组、业务值 0、「服务暂停」甚至「服务健康」。
 * 这里只做一件事：把 usePolling 的三个字段折叠成一个可穷举的状态，让「读不到」
 * 永远不能和「读到了 0」共用同一个分支。
 *
 * - loading：首次加载，还没有任何数据
 * - error  ：请求失败且没有任何旧数据可显示 —— 必须显示错误，不能显示业务默认值
 * - stale  ：请求失败但有旧数据 —— 继续显示旧数据，同时明确标注已过期
 * - empty  ：请求成功，业务上确实为空
 * - ready  ：请求成功且有内容
 */
export type RemoteState = 'loading' | 'error' | 'stale' | 'empty' | 'ready';

export interface RemoteQuery<T> {
  data: T | null;
  error: unknown;
  loading: boolean;
}

export function remoteState<T>(
  query: RemoteQuery<T>,
  isEmpty?: (data: T) => boolean,
): RemoteState {
  const { data, error, loading } = query;
  if (data === null || data === undefined) {
    if (error) return 'error';
    // usePolling 在 reject 时一定会写 error，所以「无数据、无错误、已结束」
    // 只可能是 fetcher 本身解析出了空值 —— 那是业务空态，不是失败。
    return loading ? 'loading' : 'empty';
  }
  if (error) return 'stale';
  if (isEmpty?.(data)) return 'empty';
  return 'ready';
}

/**
 * 数据是否可以被当成事实读取。
 * `stale` 也返回 true —— 旧数据仍是真实观测过的值，只是需要标注过期。
 */
export function hasFacts(state: RemoteState): boolean {
  return state === 'ready' || state === 'stale' || state === 'empty';
}
