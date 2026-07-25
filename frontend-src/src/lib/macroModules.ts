/**
 * 宏观模块标识与固定顺序 —— 叶子模块，不依赖任何其他文件。
 *
 * 单独成文件的原因：api/modules/macro.ts 与 mocks/macro.ts 互相引用
 * （api 在 mock 模式下调用 mock，mock 需要模块顺序），把这个运行时常量放在
 * 任一侧都会形成 ESM 循环并在模块求值期触发 TDZ 错误。
 * 顺序与后端 registry.MODULE_IDS 一致。
 */

export type MacroModuleId =
  | 'liquidity'
  | 'funding'
  | 'treasury'
  | 'rates'
  | 'credit'
  | 'risk'
  | 'external';

export const MACRO_MODULE_ORDER: readonly MacroModuleId[] = [
  'liquidity',
  'funding',
  'treasury',
  'rates',
  'credit',
  'risk',
  'external',
];
