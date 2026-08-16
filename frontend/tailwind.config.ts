import type { Config } from 'tailwindcss';

export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  darkMode: 'class',
  theme: {
    // FULL palette replacement (not extend): raw classes like bg-gray-100 generate
    // no CSS at all. The ESLint rule below makes them a loud error too.
    colors: {
      transparent: 'transparent',
      current: 'currentColor',
      white: '#ffffff',
      bg: 'var(--bg)',
      sidebar: 'var(--bg-sidebar)',
      subtle: 'var(--bg-subtle)',
      raised: 'var(--bg-raised)',
      line: 'var(--border)',
      'line-strong': 'var(--border-strong)',
      'line-faint': 'var(--border-faint)',
      ink: 'var(--text)',
      secondary: 'var(--text-secondary)',
      muted: 'var(--text-muted)',
      accent: 'var(--accent)',
      'accent-soft': 'var(--accent-soft)',
      'accent-on-soft': 'var(--accent-on-soft)',
      success: 'var(--success)',
      'success-soft': 'var(--success-soft)',
      danger: 'var(--danger)',
      'danger-soft': 'var(--danger-soft)',
      warning: 'var(--warning)',
      'warning-soft': 'var(--warning-soft)',
      scrim: 'var(--scrim)',
      // primary buttons invert (theme spec §2.2): near-black on light, light on dark
      primary: 'var(--text)',
      'primary-foreground': 'var(--bg)',
    },
    borderRadius: {
      none: '0',
      sm: 'var(--r-sm)',
      DEFAULT: 'var(--r-md)',
      md: 'var(--r-md)',
      lg: 'var(--r-lg)',
      xl: 'var(--r-xl)',
      full: '9999px',
    },
    extend: {
      fontFamily: {
        sans: ['Inter', 'system-ui', 'sans-serif'],
        mono: ['ui-monospace', 'JetBrains Mono', 'monospace'],
      },
      // `sm`/`md` intentionally override Tailwind's built-in hard-edged
      // defaults with the soft, low-opacity elevation tokens (theme spec
      // §2.4 refresh); `soft`/`raised` are named aliases for call sites that
      // want to read as "composer/card" vs. "menu/dialog/drawer" elevation.
      boxShadow: {
        soft: 'var(--shadow-soft)',
        sm: 'var(--shadow-sm)',
        md: 'var(--shadow-md)',
        raised: 'var(--shadow-md)',
      },
      maxWidth: { thread: '720px' },
      // Motion (design-refresh): consistent, subtle open/close easing for
      // Radix overlays — driven by data-state, using a calm panel
      // transition (cubic-bezier(0.4,0,0.2,1)). `prefers-reduced-motion` is
      // handled globally in globals.css, which forces near-zero durations.
      keyframes: {
        'overlay-in': { from: { opacity: '0' }, to: { opacity: '1' } },
        'overlay-out': { from: { opacity: '1' }, to: { opacity: '0' } },
        'dialog-in': {
          from: { opacity: '0', transform: 'translate(-50%, -50%) scale(0.98)' },
          to: { opacity: '1', transform: 'translate(-50%, -50%) scale(1)' },
        },
        'dialog-out': {
          from: { opacity: '1', transform: 'translate(-50%, -50%) scale(1)' },
          to: { opacity: '0', transform: 'translate(-50%, -50%) scale(0.98)' },
        },
        'drawer-in': { from: { transform: 'translateX(100%)' }, to: { transform: 'translateX(0)' } },
        'drawer-out': { from: { transform: 'translateX(0)' }, to: { transform: 'translateX(100%)' } },
        'menu-in': {
          from: { opacity: '0', transform: 'translateY(-2px) scale(0.98)' },
          to: { opacity: '1', transform: 'translateY(0) scale(1)' },
        },
      },
      animation: {
        'overlay-in': 'overlay-in 150ms cubic-bezier(0.4,0,0.2,1)',
        'overlay-out': 'overlay-out 120ms cubic-bezier(0.4,0,0.2,1)',
        'dialog-in': 'dialog-in 150ms cubic-bezier(0.4,0,0.2,1)',
        'dialog-out': 'dialog-out 120ms cubic-bezier(0.4,0,0.2,1)',
        'drawer-in': 'drawer-in 200ms cubic-bezier(0.4,0,0.2,1)',
        'drawer-out': 'drawer-out 160ms cubic-bezier(0.4,0,0.2,1)',
        'menu-in': 'menu-in 120ms cubic-bezier(0.4,0,0.2,1)',
      },
    },
  },
  plugins: [],
} satisfies Config;
