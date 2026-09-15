/** Node 测试用：同步装入领域词典，不进入浏览器主包。 */
import { DICT } from './dict/index.ts';
import { installTranslations, setLocale as setLocaleCore, type Locale } from './core.ts';

function tableFor(locale: 'en' | 'ja'): Record<string, string> {
  const index = locale === 'en' ? 0 : 1;
  const table: Record<string, string> = {};
  for (const [msgid, entry] of Object.entries(DICT)) {
    const text = entry[index];
    if (text) table[msgid] = text;
  }
  return table;
}

export function installTestDictionaries(): void {
  installTranslations('en', tableFor('en'));
  installTranslations('ja', tableFor('ja'));
}

export function setLocale(next: Locale): void {
  if (next !== 'zh') installTestDictionaries();
  setLocaleCore(next);
}
