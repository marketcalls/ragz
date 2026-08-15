// Shared smooth-reveal animation config for the generative-UI display charts.
//
// Charts previously hard-set `isAnimationActive={false}` everywhere, so they
// popped in with no motion. Recharts animates on mount when this is enabled —
// a soft ease-out grow/sweep, matching openui's polished feel.
//
// Disabled in two cases:
//  - under test (jsdom drives recharts' requestAnimationFrame-based animation
//    non-deterministically, which would make chart-geometry assertions flaky);
//  - when the viewer prefers reduced motion (accessibility).
const prefersReducedMotion =
  typeof window !== 'undefined' &&
  typeof window.matchMedia === 'function' &&
  window.matchMedia('(prefers-reduced-motion: reduce)').matches;

const isTest = import.meta.env.MODE === 'test';

export const CHART_ANIMATION = {
  isAnimationActive: !isTest && !prefersReducedMotion,
  animationDuration: 700,
  animationEasing: 'ease-out',
} as const;
