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
  accent: '#3b82f6',
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
    accent: v('--chart-accent', FALLBACKS.accent),
    success: v('--success', FALLBACKS.success),
    warning: v('--warning', FALLBACKS.warning),
    danger: v('--danger', FALLBACKS.danger),
    grid: v('--border', FALLBACKS.grid),
    axis: v('--text-muted', FALLBACKS.axis),
  };
}

// --- hex color math (tokens are always 6-digit hex for accent/success/warning/
// danger, light and dark alike — see styles/tokens.css) ------------------------

function hexToRgb(hex: string): [number, number, number] | null {
  const m = /^#?([0-9a-f]{6})$/i.exec(hex.trim());
  if (!m) return null;
  const int = parseInt(m[1]!, 16);
  return [(int >> 16) & 255, (int >> 8) & 255, int & 255];
}

function rgbToHex(r: number, g: number, b: number): string {
  const c = (v: number) => Math.round(Math.min(255, Math.max(0, v))).toString(16).padStart(2, '0');
  return `#${c(r)}${c(g)}${c(b)}`;
}

function rgbToHsl(r: number, g: number, b: number): [number, number, number] {
  const rn = r / 255;
  const gn = g / 255;
  const bn = b / 255;
  const max = Math.max(rn, gn, bn);
  const min = Math.min(rn, gn, bn);
  const l = (max + min) / 2;
  const d = max - min;
  if (d === 0) return [0, 0, l];
  const s = d / (1 - Math.abs(2 * l - 1));
  let h: number;
  if (max === rn) h = ((gn - bn) / d) % 6;
  else if (max === gn) h = (bn - rn) / d + 2;
  else h = (rn - gn) / d + 4;
  h *= 60;
  if (h < 0) h += 360;
  return [h, s, l];
}

function hslToRgb(h: number, s: number, l: number): [number, number, number] {
  const c = (1 - Math.abs(2 * l - 1)) * s;
  const x = c * (1 - Math.abs(((h / 60) % 2) - 1));
  const m = l - c / 2;
  let rgb: [number, number, number];
  if (h < 60) rgb = [c, x, 0];
  else if (h < 120) rgb = [x, c, 0];
  else if (h < 180) rgb = [0, c, x];
  else if (h < 240) rgb = [0, x, c];
  else if (h < 300) rgb = [x, 0, c];
  else rgb = [c, 0, x];
  return [(rgb[0] + m) * 255, (rgb[1] + m) * 255, (rgb[2] + m) * 255];
}

/** Shift a hex color's lightness a fraction of the way toward white (t > 0) or
 * black (t < 0). Non-hex input (shouldn't happen for our tokens) passes through
 * unchanged rather than throwing. */
function shiftLightness(hex: string, t: number): string {
  const rgb = hexToRgb(hex);
  if (!rgb) return hex;
  const [h, s, l] = rgbToHsl(rgb[0], rgb[1], rgb[2]);
  const target = t > 0 ? 1 : 0;
  const nl = l + (target - l) * Math.abs(t);
  const [r, g, b] = hslToRgb(h, s, nl);
  return rgbToHex(r, g, b);
}

/** Center-out selection of n colors from a ramp: short lists land on the
 * ramp's saturated middle instead of always starting at index 0, and longer
 * lists take a contiguous (wrapped) window around that center so adjacent
 * series stay visually separated. Idea ported from a reference PalletUtils
 * `getDistributedColors` helper (MIT, idea only — this is a from-scratch TS
 * port against our own tokens, not copied Recharts v2 code). */
export function getDistributedColors(colors: string[], n: number): string[] {
  if (colors.length === 0 || n <= 0) return [];
  const wrap = (i: number) => ((i % colors.length) + colors.length) % colors.length;
  const at = (i: number): string => colors[wrap(i)] as string;
  const midIndex = Math.floor(colors.length / 2);
  if (n === 1) return [at(midIndex)];
  if (n === 2) return [at(midIndex - 1), at(midIndex + 1)];
  const offset = Math.floor((n - 1) / 2);
  const result: string[] = [];
  for (let i = 0; i < n; i++) result.push(at(midIndex + (i - offset)));
  return result;
}

/** Fixed hue order (never cycled): accent, success, warning, danger. For
 * n <= 4 this is exactly the 4 in-system series colors. Beyond 4, we extend
 * the ramp with generated tints (lighter) and shades (darker) of the same 4
 * hues — grouped by lightness band, not by hue, so a centered window of any
 * width still pulls from multiple hues — and distribute n picks across it. */
export function categorical(p: ChartPalette, n: number): string[] {
  const base = [p.accent, p.success, p.warning, p.danger];
  if (n <= 4) return base.slice(0, Math.max(0, n));
  const tints = base.map((c) => shiftLightness(c, 0.32));
  const shades = base.map((c) => shiftLightness(c, -0.32));
  const ramp = [...tints, ...base, ...shades];
  return getDistributedColors(ramp, n);
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
