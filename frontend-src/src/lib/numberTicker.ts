/** Place-value keys preserve unchanged digits through 999.99 → 1,000.00. */
export function numberGlyphs(text: string) {
  const decimal = text.indexOf('.');
  let integerPlace = 0;
  const keys = new Map<number, string>();
  for (let i = (decimal < 0 ? text.length : decimal) - 1; i >= 0; i--) {
    if (/\d/.test(text[i])) keys.set(i, `integer-${integerPlace++}`);
  }
  return [...text].map((char, index) => ({ char, key: keys.get(index) ?? (decimal >= 0 && index > decimal && /\d/.test(char) ? `decimal-${index - decimal}` : `literal-${text.length - index}-${char}`) }));
}
