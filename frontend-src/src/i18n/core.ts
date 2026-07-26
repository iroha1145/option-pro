/**
 * 多语言内核（简体中文 / English / 日本語）
 *
 * 设计取舍：
 * - 词条以「简体中文原文」为 msgid（gettext 风格）。源码里读得懂、缺译时如实回退中文原文，
 *   且后端下发的中文标签（板块名、形态标签、公司名…）可以走同一张表，不需要第二套机制。
 * - 语言在「页面加载期」定型：切换语言 = 落盘 + 整页重载。模块级常量、useMemo 缓存的
 *   文案都在加载期求值，只有重载才能保证全站文案不出现半中半日的混排。
 * - 词典静态引入（非按需 import）：t() 在模块顶层就可能被调用，异步加载会出现
 *   「常量已求值、词典还没到」的竞态。
 * - AI 生成的正文（新闻分析、财报影响、焦点周期摘要、期权解读…）一律不进词典、
 *   不经 t()：那是模型当次写下的内容而非界面文案，翻译会失真，如实保留原文。
 */
// 完整相对路径 + 扩展名（而不是 './dict' 目录导入）：node --experimental-strip-types
// 跑测试时按 ES 模块规范解析，不会像 Vite/CommonJS 那样把目录自动接到 index.ts。
import { DICT } from './dict/index.ts';

export type Locale = 'zh' | 'en' | 'ja';

export interface LocaleMeta {
  code: Locale;
  /** 切换器按钮上的短码 */
  short: string;
  /** 菜单里的母语名 */
  native: string;
  /** <html lang> 与 Intl 用的 BCP 47 标签 */
  tag: string;
}

export const LOCALES: readonly LocaleMeta[] = [
  { code: 'en', short: 'EN', native: 'English', tag: 'en-US' },
  { code: 'ja', short: 'JA', native: '日本語', tag: 'ja-JP' },
  { code: 'zh', short: 'ZH', native: '简体中文', tag: 'zh-CN' },
] as const;

const STORAGE_KEY = 'optix:locale';

function isLocale(value: unknown): value is Locale {
  return value === 'zh' || value === 'en' || value === 'ja';
}

function stored(): Locale | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    return isLocale(raw) ? raw : null;
  } catch {
    return null; // 隐私模式下 localStorage 可能直接抛错
  }
}

/** 浏览器语言优先级：ja → zh → en，其余语言一律回退英文 */
export function detectLocale(tags: readonly (string | undefined)[]): Locale {
  for (const raw of tags) {
    if (!raw) continue;
    const tag = raw.toLowerCase();
    if (tag.startsWith('ja')) return 'ja';
    if (tag.startsWith('zh')) return 'zh';
    if (tag.startsWith('en')) return 'en';
  }
  return 'en';
}

function detectFromNavigator(): Locale {
  // 判「没有浏览器」不能只看 navigator：Node 21+ 自带一个全局 navigator
  // （navigator.language 恒为 "en-US"，与真实用户毫无关系），用它判断会把
  // 「压根没有浏览器」误判成「浏览器报告英文」。window 仍然是浏览器独有的，
  // 本文件别处（setLocale/applyHtmlLang）已经用它做过同样的判断，这里同口径。
  // 没有浏览器时回退中文原文，呼应 t() 本身「缺译时如实回退中文」的原则——
  // 本项目测试套件大量直接 import 源码跑真实逻辑，用中文原文当基准。
  if (typeof window === 'undefined') return 'zh';
  return detectLocale([navigator.language, ...(navigator.languages ?? [])]);
}

let current: Locale = stored() ?? detectFromNavigator();

export function getLocale(): Locale {
  return current;
}

export function localeMeta(code: Locale = current): LocaleMeta {
  return LOCALES.find((l) => l.code === code) ?? LOCALES[0];
}

/** Intl 用的语言标签（日期、数字本地化） */
export function localeTag(code: Locale = current): string {
  return localeMeta(code).tag;
}

export type TVars = Record<string, string | number | null | undefined>;

function interpolate(template: string, vars: TVars): string {
  return template.replace(/\{(\w+)\}/g, (whole, key: string) => {
    const v = vars[key];
    return v === undefined || v === null ? whole : String(v);
  });
}

/**
 * 复数形式：词条写成 `单数||复数`，仅当 vars.n 是有限数字时按 n === 1 取单数。
 * 中日文没有复数变化，只有英文词条需要写双竖线。
 */
function plural(text: string, vars?: TVars): string {
  if (!text.includes('||')) return text;
  const [one, other] = text.split('||');
  const n = vars?.n;
  return typeof n === 'number' && Number.isFinite(n) && Math.abs(n) === 1 ? one : other;
}

const missing = new Set<string>();

/**
 * 取译文。msgid 为简体中文原文；未收录时回退原文（AI 生成的中文正文即走此分支，
 * 属预期行为——那是模型写下的内容，不是界面文案）。
 */
export function t(msgid: string, vars?: TVars): string {
  let text = msgid;
  if (current !== 'zh') {
    const entry = DICT[msgid];
    const translated = entry?.[current === 'en' ? 0 : 1];
    if (translated) text = translated;
    else if (import.meta.env?.DEV && !missing.has(msgid)) {
      missing.add(msgid);
      console.warn(`[i18n] 缺 ${current} 译文：${msgid}`);
    }
  }
  text = plural(text, vars);
  return vars ? interpolate(text, vars) : text;
}

/** 该 msgid 是否有当前语言的译文（用于「AI 正文语言」这类如实标注） */
export function hasTranslation(msgid: string, code: Locale = current): boolean {
  if (code === 'zh') return true;
  return Boolean(DICT[msgid]?.[code === 'en' ? 0 : 1]);
}

function applyHtmlLang(code: Locale): void {
  if (typeof document === 'undefined') return;
  document.documentElement.lang = localeMeta(code).tag;
}

applyHtmlLang(current);

/**
 * 切换语言：落盘后整页重载。重载保留当前 URL，用户停在原来的页面上。
 */
export function setLocale(next: Locale): void {
  if (next === current) return;
  try {
    localStorage.setItem(STORAGE_KEY, next);
  } catch {
    /* 存不下就只影响「记住选择」，本次切换照常生效 */
  }
  current = next;
  applyHtmlLang(next);
  if (typeof window !== 'undefined') window.location.reload();
}
