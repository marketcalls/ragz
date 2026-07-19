import { readChartPalette, categorical } from './chart-palette';

it('reads semantic tokens with fallbacks', () => {
  document.documentElement.style.setProperty('--accent', '#123456');
  expect(readChartPalette().accent).toBe('#123456');
  document.documentElement.style.removeProperty('--accent');
  expect(readChartPalette().accent).toBe('#4f46e5'); // spec fallback
});

it('categorical caps at 4 in-system colors', () => {
  const p = readChartPalette();
  expect(categorical(p, 9)).toHaveLength(4);
});
