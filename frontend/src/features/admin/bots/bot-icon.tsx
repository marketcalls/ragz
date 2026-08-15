// Vite: import every bundled bot-channel SVG as a URL, keyed by filename (platform).
const ICONS = import.meta.glob('./bot-icons/*.svg', {
  eager: true,
  query: '?url',
  import: 'default',
}) as Record<string, string>;

export function BotIcon({
  platform,
  className = 'h-8 w-8',
}: {
  platform: string;
  className?: string;
}) {
  const url = ICONS[`./bot-icons/${platform}.svg`];
  if (url) {
    return (
      <img
        src={url}
        alt={platform}
        role="img"
        className={`${className} rounded-lg object-contain`}
      />
    );
  }
  const monogram = platform.trim().slice(0, 1).toUpperCase() || '?';
  return (
    <span
      role="img"
      aria-label={platform}
      className={`${className} inline-flex items-center justify-center rounded-lg border border-line bg-subtle text-sm font-semibold text-ink`}
    >
      {monogram}
    </span>
  );
}
