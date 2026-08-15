// Vite: import every bundled provider SVG as a URL, keyed by filename (id).
const ICONS = import.meta.glob('./provider-icons/*.svg', {
  eager: true,
  query: '?url',
  import: 'default',
}) as Record<string, string>;

function iconUrl(id: string): string | undefined {
  return ICONS[`./provider-icons/${id}.svg`];
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
  const monogram = provider.name.trim().slice(0, 1).toUpperCase() || '?';
  return (
    <span
      role="img"
      aria-label={provider.name}
      className={`${className} inline-flex items-center justify-center rounded-lg border border-line bg-subtle text-sm font-semibold text-ink`}
    >
      {monogram}
    </span>
  );
}
