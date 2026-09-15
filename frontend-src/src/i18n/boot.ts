/**
 * 在任何依赖模块级 t() 的应用代码执行前装入当前语言词典。
 * 中文不下载译文；英/日各自只装入一种语言。
 */
import { applyDocumentLocale, getLocale, installTranslations, type Locale } from './core.ts';

async function loadTable(locale: Locale): Promise<Record<string, string>> {
  if (locale === 'en') {
    const { EN } = await import('./dict/runtime-en.ts');
    return EN;
  }
  const { JA } = await import('./dict/runtime-ja.ts');
  return JA;
}

export async function prepareI18n(): Promise<void> {
  const locale = getLocale();
  if (locale !== 'zh') {
    try {
      installTranslations(locale, await loadTable(locale));
    } catch {
      /* 词典加载失败时继续启动，t() 回退中文原文 */
    }
  }
  applyDocumentLocale();
}
