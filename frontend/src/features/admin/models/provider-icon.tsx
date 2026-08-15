// Vite: import every bundled provider SVG as a URL, keyed by filename (id).
// Drop an official `<provider-id>.svg` into ./provider-icons/ and it is used
// automatically (takes precedence over the branded initial below).
const ICONS = import.meta.glob('./provider-icons/*.svg', {
  eager: true,
  query: '?url',
  import: 'default',
}) as Record<string, string>;

function iconUrl(id: string): string | undefined {
  return ICONS[`./provider-icons/${id}.svg`];
}

// Curated accent colors for popular providers so their fallback tile reads as
// a branded chip, not an anonymous grey box. These are plain background colors
// for an initial-letter tile -- NOT reproductions of any company logo. Keyed by
// the provider `id` (slug) used in provider-catalog.ts.
const BRAND_COLOR: Record<string, string> = {
  openai: '#10a37f',
  anthropic: '#cc785c',
  gemini: '#1a73e8',
  'azure-openai': '#0078d4',
  bedrock: '#ff9900',
  baseten: '#6366f1',
  'fireworks-ai': '#8b5cf6',
  cerebras: '#ea580c',
  sambanova: '#ee3124',
  hyperbolic: '#10b981',
  lambda: '#4028a0',
  nebius: '#1d4ed8',
  groq: '#f55036',
  togetherai: '#0f6fff',
  deepseek: '#4d6bfe',
  deepinfra: '#4f46e5',
  mistral: '#fa5310',
  cohere: '#39594d',
  perplexity: '#20808d',
  openrouter: '#6467f2',
  novitaai: '#10b981',
  moonshot: '#111827',
  minimax: '#ff4d4f',
  'zhipu-ai': '#3859ff',
  'voyage-ai': '#5b21b6',
  nvidia: '#76b900',
  huggingface: '#ff9d00',
  replicate: '#ef4444',
  jina: '#eab308',
  xai: '#111827',
  ollama: '#111827',
  'tongyi-qianwen': '#615ced',
  'tencent-hunyuan': '#1476ff',
  volcengine: '#1664ff',
  siliconflow: '#7c3aed',
};

// Stable fallback palette for providers without a curated color: hash the id so
// each provider keeps the same tile color across renders (never random).
const PALETTE = [
  '#2563eb', '#7c3aed', '#db2777', '#dc2626', '#ea580c', '#d97706',
  '#16a34a', '#0891b2', '#4f46e5', '#9333ea', '#0d9488', '#c026d3',
];

function paletteFor(id: string): string {
  let h = 0;
  for (let i = 0; i < id.length; i += 1) h = (h * 31 + id.charCodeAt(i)) >>> 0;
  return PALETTE[h % PALETTE.length] ?? '#2563eb';
}

// Relative luminance -> pick black or white text so the initial stays legible
// on light brand colors (e.g. NVIDIA green, Jina yellow) as well as dark ones.
function textOn(hex: string): string {
  const n = hex.replace('#', '');
  const r = parseInt(n.slice(0, 2), 16) / 255;
  const g = parseInt(n.slice(2, 4), 16) / 255;
  const b = parseInt(n.slice(4, 6), 16) / 255;
  const lin = (c: number) => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4);
  const lum = 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b);
  return lum > 0.5 ? '#111827' : '#ffffff';
}

export function ProviderIcon({
  provider,
  className = 'h-8 w-8',
}: {
  provider: { id: string; name: string; icon: string };
  className?: string;
}) {
  const url = iconUrl(provider.icon) ?? iconUrl(provider.id);
  if (url) {
    return (
      <img
        src={url}
        alt={provider.name}
        role="img"
        className={`${className} rounded-lg object-contain`}
      />
    );
  }
  // No bundled SVG: a branded initial tile (provider accent color + first
  // letter). Distinct per provider, legally safe (not a logo reproduction).
  const bg = BRAND_COLOR[provider.id] ?? paletteFor(provider.id);
  const monogram = provider.name.trim().slice(0, 1).toUpperCase() || '?';
  return (
    <span
      role="img"
      aria-label={provider.name}
      style={{ backgroundColor: bg, color: textOn(bg) }}
      className={`${className} inline-flex items-center justify-center rounded-lg text-sm font-semibold`}
    >
      {monogram}
    </span>
  );
}
