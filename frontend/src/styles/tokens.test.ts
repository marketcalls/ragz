// @vitest-environment node
// (jsdom's global URL polyfill resolves relative file URLs to `http:` instead
// of `file:`, breaking readFileSync below — this file only reads tokens.css
// from disk, so it needs no DOM and runs fine under the node environment.)
import { readFileSync } from 'node:fs';

const css = readFileSync(new URL('./tokens.css', import.meta.url), 'utf8');
const [lightBlock = '', darkBlock = ''] = css.split('.dark');

const LIGHT: Record<string, string> = {
  '--bg': '#ffffff',
  '--bg-sidebar': '#f9f9f9',
  '--bg-subtle': '#f4f4f5',
  '--bg-raised': '#fafafa',
  '--border': 'rgba(23, 23, 23, 0.08)',
  '--border-strong': 'rgba(23, 23, 23, 0.16)',
  '--border-faint': 'rgba(23, 23, 23, 0.05)',
  '--text': '#171717',
  '--text-secondary': '#555555',
  '--text-muted': '#8a8a8a',
  '--accent': '#4f46e5',
  '--accent-soft': '#eef2ff',
  '--success': '#059669',
  '--success-soft': '#ecfdf5',
  '--danger': '#dc2626',
  '--danger-soft': '#fef2f2',
  '--warning': '#b45309',
  '--warning-soft': '#fffbeb',
  '--scrim': 'rgba(23, 23, 23, 0.4)',
};

const DARK: Record<string, string> = {
  '--bg': '#181818',
  '--bg-sidebar': '#111113',
  '--bg-subtle': '#26262a',
  '--bg-raised': '#1d1d20',
  '--border': 'rgba(236, 236, 236, 0.1)',
  '--border-strong': 'rgba(236, 236, 236, 0.18)',
  '--border-faint': 'rgba(236, 236, 236, 0.06)',
  '--text': '#ececec',
  '--text-secondary': '#a7a7ad',
  '--text-muted': '#7a7a80',
  '--accent': '#818cf8',
  '--accent-on-soft': '#a5b4fc',
  '--accent-soft': 'rgba(129, 140, 248, 0.18)',
  '--scrim': 'rgba(0, 0, 0, 0.6)',
};

test.each(Object.entries(LIGHT))('light token %s = %s', (name, value) => {
  expect(lightBlock).toContain(`${name}: ${value}`);
});

test.each(Object.entries(DARK))('dark token %s = %s', (name, value) => {
  expect(darkBlock).toContain(`${name}: ${value}`);
});

test('radii per theme spec §2.4', () => {
  for (const decl of ['--r-sm: 6px', '--r-md: 8px', '--r-lg: 10px', '--r-xl: 16px']) {
    expect(lightBlock).toContain(decl);
  }
});

test('elevation tokens: --shadow-soft aliases --shadow-sm in both themes', () => {
  expect(lightBlock).toContain('--shadow-soft: var(--shadow-sm)');
  expect(darkBlock).toContain('--shadow-soft: var(--shadow-sm)');
  expect(lightBlock).toContain('--shadow-md:');
  expect(darkBlock).toContain('--shadow-md:');
});
