/** Generate language-only runtime dictionaries from domain [en, ja] files. */
import { writeFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { DICT } from '../src/i18n/dict/index.ts';

const here = path.dirname(fileURLToPath(import.meta.url));
const dictDir = path.join(here, '..', 'src', 'i18n', 'dict');

function dump(name, index) {
  const lines = Object.entries(DICT).flatMap(([key, entry]) => {
    const text = entry[index];
    return text ? [`  ${JSON.stringify(key)}: ${JSON.stringify(text)},`] : [];
  });
  return [
    '/** Generated from dict domain files. Do not edit by hand; run scripts/sync-i18n-runtime.mjs. */',
    `export const ${name}: Record<string, string> = {`,
    ...lines,
    '};',
    '',
  ].join('\n');
}

await writeFile(path.join(dictDir, 'runtime-en.ts'), dump('EN', 0));
await writeFile(path.join(dictDir, 'runtime-ja.ts'), dump('JA', 1));
