import { FileText, Globe } from 'lucide-react';
import { useState } from 'react';

import type { SourceRefItem, SourceRefsBlock as SourceRefsBlockT } from '@/api/types';

// Copied from behind-the-scenes.tsx (openui-parity design 2026-08-16, §6):
// the same http(s)-only guard, re-applied at render time. The backend
// already validates SourceRef.url as http(s)-only, but this renderer never
// trusts a model-authored field without re-checking (Iron Rule 5).
function isHttpUrl(url: string | null | undefined): url is string {
  if (!url) return false;
  try {
    const parsed = new URL(url);
    return parsed.protocol === 'http:' || parsed.protocol === 'https:';
  } catch {
    return false;
  }
}

function hostnameOf(item: SourceRefItem): string {
  if (item.source) return item.source;
  if (item.url) {
    try {
      return new URL(item.url).hostname;
    } catch {
      return '';
    }
  }
  return '';
}

// Favicon for a web item's hostname -- same DuckDuckGo icon-service pattern
// as behind-the-scenes.tsx's Favicon (privacy-respecting, no-referrer),
// falling back to a neutral globe glyph on 404/blank host.
function Favicon({ hostname }: { hostname: string }) {
  const [failed, setFailed] = useState(false);
  if (!hostname || failed) {
    return <Globe className="h-4 w-4 shrink-0 text-muted" aria-hidden />;
  }
  return (
    <img
      src={`https://icons.duckduckgo.com/ip3/${hostname}.ico`}
      alt=""
      aria-hidden
      width={16}
      height={16}
      loading="lazy"
      referrerPolicy="no-referrer"
      onError={() => setFailed(true)}
      className="h-4 w-4 shrink-0 rounded-sm"
    />
  );
}

// image_ref resolves through the server-side media proxy (Phase 3 of the
// design doc) -- until that lands, image_ref is normally absent and this
// never renders; onError just hides a broken image rather than showing a
// placeholder icon, since a favicon/glyph already covers that role above.
function RefImage({ imageRef }: { imageRef: string }) {
  const [failed, setFailed] = useState(false);
  if (failed) return null;
  return (
    <img
      src={`/api/v1/media/image/${imageRef}`}
      alt=""
      loading="lazy"
      referrerPolicy="no-referrer"
      onError={() => setFailed(true)}
      className="mb-2 h-24 w-full rounded-md object-cover"
    />
  );
}

function SourceRefCard({
  item,
  onOpenDocument,
}: {
  item: SourceRefItem;
  onOpenDocument?: (item: SourceRefItem) => void;
}) {
  const hostname = hostnameOf(item);
  const url = item.url;
  const web = isHttpUrl(url);
  const body = (
    <>
      {item.image_ref ? <RefImage imageRef={item.image_ref} /> : null}
      <div className="mb-1 flex items-center gap-1.5 text-[11px] text-muted">
        {web ? (
          <Favicon hostname={hostname} />
        ) : (
          <FileText className="h-4 w-4 shrink-0 text-muted" aria-hidden />
        )}
        <span className="min-w-0 truncate">{hostname || (web ? 'web' : 'document')}</span>
      </div>
      <p className="text-[12.5px] font-medium text-ink">{item.title}</p>
    </>
  );

  if (isHttpUrl(url)) {
    return (
      <a
        href={url}
        target="_blank"
        rel="noreferrer noopener"
        className="block rounded-md border border-line bg-bg p-2.5 hover:border-accent"
      >
        {body}
      </a>
    );
  }
  // Doc item (or a malformed/non-http url with no usable link): open the
  // in-app document viewer via the caller-supplied handler.
  return (
    <button
      type="button"
      onClick={() => onOpenDocument?.(item)}
      className="block w-full rounded-md border border-line bg-bg p-2.5 text-left hover:border-accent"
    >
      {body}
    </button>
  );
}

export function SourceRefsView({
  block,
  onOpenDocument,
}: {
  block: SourceRefsBlockT;
  onOpenDocument?: (item: SourceRefItem) => void;
}) {
  if (block.items.length === 0) return null;
  return (
    <div>
      <h3 className="mb-2 text-sm font-semibold text-ink">{block.title ?? 'Sources'}</h3>
      <div className="grid gap-2 sm:grid-cols-2">
        {block.items.map((item, i) => (
          <SourceRefCard key={i} item={item} onOpenDocument={onOpenDocument} />
        ))}
      </div>
    </div>
  );
}
