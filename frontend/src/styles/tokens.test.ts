// @vitest-environment node
// (jsdom's global URL polyfill resolves relative file URLs to `http:` instead
// of `file:`, breaking readFileSync below — this file only reads tokens.css
// from disk, so it needs no DOM and runs fine under the node environment.)
import { readFileSync } from 'node:fs';

const css = readFileSync(new URL('./tokens.css', import.meta.url), 'utf8');
const [lightBlock = '', darkBlock = ''] = css.split('.dark');

// openui theme (§2). Surfaces layered off-white/white (light) & near-black
// (dark); monochrome interactive accent; ~6% alpha hairlines.
const LIGHT: Record<string, string> = {
  '--bg': '#f6f6f5',
  '--bg-sidebar': '#f1f1ef',
  '--bg-subtle': '#ececeb',
  '--bg-raised': '#ffffff',
  '--border': 'rgba(23, 23, 23, 0.06)',
  '--border-strong': 'rgba(23, 23, 23, 0.12)',
  '--border-faint': 'rgba(23, 23, 23, 0.04)',
  '--text': '#171717',
  '--text-secondary': 'rgba(23, 23, 23, 0.56)',
  '--text-muted': 'rgba(23, 23, 23, 0.4)',
  '--accent': '#171717',
  '--accent-soft': 'rgba(23, 23, 23, 0.06)',
  '--chart-accent': '#3b82f6',
  '--success': '#1f8a4c',
  '--danger': '#d23a34',
  '--warning': '#9a6a16',
  '--scrim': 'rgba(0, 0, 0, 0.4)',
};

const DARK: Record<string, string> = {
  '--bg': '#1a1a1a',
  '--bg-sidebar': '#141414',
  '--bg-subtle': '#262626',
  '--bg-raised': '#232323',
  '--border': 'rgba(250, 250, 250, 0.08)',
  '--border-strong': 'rgba(250, 250, 250, 0.14)',
  '--border-faint': 'rgba(250, 250, 250, 0.05)',
  '--text': '#fafafa',
  '--text-secondary': 'rgba(250, 250, 250, 0.56)',
  '--text-muted': 'rgba(250, 250, 250, 0.4)',
  '--accent': '#fafafa',
  '--accent-on-soft': '#fafafa',
  '--accent-soft': 'rgba(250, 250, 250, 0.09)',
  '--chart-accent': '#60a5fa',
  '--scrim': 'rgba(0, 0, 0, 0.6)',
};

test.each(Object.entries(LIGHT))('light token %s = %s', (name, value) => {
  expect(lightBlock).toContain(`${name}: ${value}`);
});

test.each(Object.entries(DARK))('dark token %s = %s', (name, value) => {
  expect(darkBlock).toContain(`${name}: ${value}`);
});

test('radii per theme spec §2.4', () => {
  for (const decl of ['--r-sm: 6px', '--r-md: 8px', '--r-lg: 10px', '--r-xl: 14px']) {
    expect(lightBlock).toContain(decl);
  }
});

test('elevation tokens: --shadow-soft aliases --shadow-sm in both themes', () => {
  expect(lightBlock).toContain('--shadow-soft: var(--shadow-sm)');
  expect(darkBlock).toContain('--shadow-soft: var(--shadow-sm)');
  expect(lightBlock).toContain('--shadow-md:');
  expect(darkBlock).toContain('--shadow-md:');
});
