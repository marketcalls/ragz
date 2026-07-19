// Chart colors come EXCLUSIVELY from theme tokens (theme spec §6): accent for
// the primary series, status colors for additional series, border/muted for
// chrome. Recharts sets SVG presentation attributes, which don't resolve
// var() — so we read computed values and re-read when the theme class flips.
import { useEffect, useState } from 'react';

export interface ChartPalette {
  accent: string;
  success: string;
  warning: string;
  danger: string;
  grid: string;
  axis: string;
}

const FALLBACKS: ChartPalette = {
  accent: '#4f46e5',
  success: '#059669',
  warning: '#b45309',
  danger: '#dc2626',
  grid: '#ececec',
  axis: '#8a8a8a',
};

export function readChartPalette(): ChartPalette {
  const s = getComputedStyle(document.documentElement);
  const v = (name: string, fallback: string): string =>
    s.getPropertyValue(name).trim() || fallback;
  return {
    accent: v('--accent', FALLBACKS.accent),
    success: v('--success', FALLBACKS.success),
    warning: v('--warning', FALLBACKS.warning),
    danger: v('--danger', FALLBACKS.danger),
    grid: v('--border', FALLBACKS.grid),
    axis: v('--text-muted', FALLBACKS.axis),
  };
}

/** Up to 4 series colors, all in-system. Bucket longer tails into "Other" first. */
export function categorical(p: ChartPalette, n: number): string[] {
  return [p.accent, p.success, p.warning, p.danger].slice(0, Math.min(n, 4));
}

export function useChartPalette(): ChartPalette {
  const [palette, setPalette] = useState(readChartPalette);
  useEffect(() => {
    const observer = new MutationObserver(() => setPalette(readChartPalette()));
    observer.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ['class'],
    });
    return () => observer.disconnect();
  }, []);
  return palette;
}
