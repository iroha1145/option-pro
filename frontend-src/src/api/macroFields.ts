/**
 * 宏观影子字段的共享映射。
 *
 * 单独一个模块，而不是挂在 strength.ts 上：股票、板块、突破、个股详情四处都要用
 * 它，而 strength.ts 会带进 mock fixture —— 为一个映射函数让另外三个 chunk 都拖上
 * fixture，正好和「把 mock 假数据赶出 live 产物」这件事反着来。
 */
import { asRec, pickS } from './live';
import type { MacroFitDriver } from '@/lib/macroFit';

/**
 * 宏观驱动因素。后端下发 `{factor_id, label}`，中文名在 registry 解析。
 *
 * 前端刻意不留一份 id → 中文名的映射表：那是同一个事实的第二份拷贝，因子改名之后
 * 界面会继续显示旧名字，而且什么都不会失败。
 *
 * 同时接受裸字符串数组：磁盘上的强度快照在 Worker 重跑之前仍是旧形状。那时显示
 * id 是诚实的 —— 那正是旧快照携带的全部信息；丢成空列表会让面板说「各宏观因子
 * 方向不明显」，那是一句关于数据的判断，不是「读不到名字」。
 */
export function mapMacroFitDrivers(raw: unknown): MacroFitDriver[] {
  if (!Array.isArray(raw)) return [];
  return raw.flatMap((item) => {
    if (typeof item === 'string') {
      const id = item.trim();
      return id ? [{ factor_id: id, label: id }] : [];
    }
    const rec = asRec(item);
    const factorId = pickS(rec, 'factor_id');
    if (!factorId) return [];
    return [{ factor_id: factorId, label: pickS(rec, 'label') ?? factorId }];
  });
}
