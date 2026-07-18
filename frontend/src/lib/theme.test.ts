import { applyTheme, resolveInitialTheme, storeTheme } from './theme';

afterEach(() => {
  localStorage.clear();
  document.documentElement.classList.remove('dark');
});

test('stored theme wins over media preference', () => {
  storeTheme('dark');
  expect(resolveInitialTheme()).toBe('dark');
});

test('falls back to prefers-color-scheme', () => {
  vi.stubGlobal(
    'matchMedia',
    vi.fn((q: string) => ({ matches: q.includes('dark'), addEventListener: vi.fn(), removeEventListener: vi.fn() })),
  );
  expect(resolveInitialTheme()).toBe('dark');
  vi.unstubAllGlobals();
});

test('applyTheme toggles the .dark class on <html>', () => {
  applyTheme('dark');
  expect(document.documentElement.classList.contains('dark')).toBe(true);
  applyTheme('light');
  expect(document.documentElement.classList.contains('dark')).toBe(false);
});
