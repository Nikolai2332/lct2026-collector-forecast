/** Форма слова для числа: plural(1, 'датчик', 'датчика', 'датчиков') → «датчик», 5 → «датчиков» */
export function plural(n: number, one: string, few: string, many: string): string {
  const a = Math.abs(Math.trunc(n)) % 100;
  const b = a % 10;
  if (a > 10 && a < 20) return many;
  if (b === 1) return one;
  if (b >= 2 && b <= 4) return few;
  return many;
}

/** «5 датчиков», «1 отказ» */
export const nWord = (n: number, one: string, few: string, many: string): string =>
  `${n.toLocaleString('ru-RU')} ${plural(n, one, few, many)}`;

export const sensorsWord = (n: number) => nWord(n, 'датчик', 'датчика', 'датчиков');
