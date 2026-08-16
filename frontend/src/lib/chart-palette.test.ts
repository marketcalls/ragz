import { categorical, getDistributedColors, readChartPalette } from './chart-palette';

it('reads semantic tokens with fallbacks', () => {
  document.documentElement.style.setProperty('--chart-accent', '#123456');
  expect(readChartPalette().accent).toBe('#123456');
  document.documentElement.style.removeProperty('--chart-accent');
  expect(readChartPalette().accent).toBe('#3b82f6'); // chart-accent fallback
});

describe('categorical', () => {
  it('returns the fixed accent/success/warning/danger order for n <= 4', () => {
    const p = readChartPalette();
    expect(categorical(p, 1)).toEqual([p.accent]);
    expect(categorical(p, 3)).toEqual([p.accent, p.success, p.warning]);
    expect(categorical(p, 4)).toEqual([p.accent, p.success, p.warning, p.danger]);
  });

  it('extends beyond 4 with distinct generated colors', () => {
    const p = readChartPalette();
    const colors = categorical(p, 8);
    expect(colors).toHaveLength(8);
    expect(new Set(colors).size).toBe(8);
  });

  it('stays theme-aware: re-reading the palette after a token change changes the ramp', () => {
    const before = categorical(readChartPalette(), 8);
    document.documentElement.style.setProperty('--chart-accent', '#123456');
    const after = categorical(readChartPalette(), 8);
    document.documentElement.style.removeProperty('--chart-accent');
    expect(after).toContain('#123456');
    expect(after).not.toEqual(before);
  });

  it('handles n = 0 gracefully', () => {
    expect(categorical(readChartPalette(), 0)).toEqual([]);
  });
});

describe('getDistributedColors', () => {
  it('picks the ramp middle for a single color', () => {
    expect(getDistributedColors(['a', 'b', 'c', 'd', 'e'], 1)).toEqual(['c']);
  });

  it('straddles the middle for two colors', () => {
    expect(getDistributedColors(['a', 'b', 'c', 'd', 'e'], 2)).toEqual(['b', 'd']);
  });

  it('returns n distinct colors when n is within the ramp length', () => {
    const ramp = ['a', 'b', 'c', 'd', 'e', 'f', 'g', 'h'];
    const result = getDistributedColors(ramp, 5);
    expect(result).toHaveLength(5);
    expect(new Set(result).size).toBe(5);
  });

  it('wraps around ramp bounds instead of throwing', () => {
    expect(getDistributedColors(['a', 'b', 'c'], 3)).toHaveLength(3);
  });

  it('returns an empty array for an empty ramp or n <= 0', () => {
    expect(getDistributedColors([], 3)).toEqual([]);
    expect(getDistributedColors(['a', 'b'], 0)).toEqual([]);
  });
});
