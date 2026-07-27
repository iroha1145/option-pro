/**
 * 词条形状：以简体中文原文为键，值是 [English, 日本語]。
 * 缺译（空串或缺键）时 t() 回退中文原文，不会渲染出空白。
 */
export type DictEntry = readonly [en: string, ja: string];
export type Dict = Record<string, DictEntry>;
