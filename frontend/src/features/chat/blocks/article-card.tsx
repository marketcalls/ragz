import { useState } from 'react';

import type { ArticleCardBlock as ArticleCardBlockT } from '@/api/types';
import { Markdown } from '@/components/markdown/markdown';
import { cn } from '@/lib/cn';

// Copied guard (see source-refs.tsx / behind-the-scenes.tsx): a `url` field
// is only ever rendered as a real href after re-checking it's http(s) here,
// regardless of what the (already server-validated) block payload claims.
function isHttpUrl(url: string | null | undefined): url is string {
  if (!url) return false;
  try {
    const parsed = new URL(url);
    return parsed.protocol === 'http:' || parsed.protocol === 'https:';
  } catch {
    return false;
  }
}

// Mirrors block-renderer.tsx's TagBadges tone->class map. Duplicated (not
// imported) to avoid a circular import between block-renderer.tsx (which
// imports this component) and this module -- keep the two in sync manually.
const TAG_TONES: Record<string, string> = {
  neutral: 'bg-subtle text-secondary',
  info: 'bg-accent-soft text-accent-on-soft',
  success: 'bg-success-soft text-success',
  warning: 'bg-warning-soft text-warning',
  danger: 'bg-danger-soft text-danger',
};

// image_ref resolves through the server-side media proxy (Phase 3 of the
// design doc); onError hides the image entirely (caller falls back to a
// gradient placeholder / no image, never a broken-image icon).
function CardImage({ imageRef, className }: { imageRef: string; className: string }) {
  const [failed, setFailed] = useState(false);
  if (failed) return null;
  return (
    <img
      src={`/api/v1/media/image/${imageRef}`}
      alt=""
      loading="lazy"
      referrerPolicy="no-referrer"
      onError={() => setFailed(true)}
      className={className}
    />
  );
}

function Tags({ tags }: { tags: ArticleCardBlockT['tags'] }) {
  if (!tags || tags.length === 0) return null;
  return (
    <div className="mt-2 flex flex-wrap gap-1.5">
      {tags.map((tag, i) => (
        <span
          key={i}
          className={cn(
            'inline-flex items-center rounded-full px-2 py-0.5 text-[12px] font-medium',
            TAG_TONES[tag.tone] ?? TAG_TONES.neutral,
          )}
        >
          {tag.label}
        </span>
      ))}
    </div>
  );
}

function HeroCard({
  block,
  onOpenDocument,
}: {
  block: ArticleCardBlockT;
  onOpenDocument?: () => void;
}) {
  const url = block.url;
  const frame = (
    <div className="relative h-40 w-full overflow-hidden rounded-lg">
      {block.image_ref ? (
        <CardImage imageRef={block.image_ref} className="h-full w-full object-cover" />
      ) : (
        <div className="h-full w-full bg-gradient-to-br from-accent-soft to-subtle" aria-hidden />
      )}
      {block.badge ? (
        <span className="absolute left-2 top-2 inline-flex items-center rounded-full bg-bg/90 px-2 py-0.5 text-[11px] font-medium text-secondary">
          {block.badge}
        </span>
      ) : null}
      <div className="absolute inset-x-0 bottom-0 bg-gradient-to-t from-black/70 to-transparent p-3">
        <h3 className="text-sm font-semibold text-white">{block.title}</h3>
        {block.subtitle ? <p className="mt-0.5 text-[12px] text-white/80">{block.subtitle}</p> : null}
      </div>
    </div>
  );

  const className = 'block overflow-hidden rounded-lg border border-line';
  if (isHttpUrl(url)) {
    return (
      <a href={url} target="_blank" rel="noreferrer noopener" className={className}>
        {frame}
      </a>
    );
  }
  if (block.document_id) {
    return (
      <button type="button" onClick={onOpenDocument} className={cn(className, 'w-full text-left')}>
        {frame}
      </button>
    );
  }
  return <div className={className}>{frame}</div>;
}

function StandardCard({
  block,
  onOpenDocument,
}: {
  block: ArticleCardBlockT;
  onOpenDocument?: () => void;
}) {
  const url = block.url;
  const web = isHttpUrl(url);
  const hasLink = web || !!block.document_id;
  return (
    <div className="overflow-hidden rounded-lg border border-line bg-bg">
      {block.image_ref ? (
        <CardImage imageRef={block.image_ref} className="h-32 w-full object-cover" />
      ) : null}
      <div className="p-3">
        {block.badge ? (
          <span className="mb-1.5 inline-flex items-center rounded-full bg-subtle px-2 py-0.5 text-[11px] font-medium text-secondary">
            {block.badge}
          </span>
        ) : null}
        <h3 className="text-sm font-semibold text-ink">{block.title}</h3>
        {block.subtitle ? <p className="mt-0.5 text-[12px] text-secondary">{block.subtitle}</p> : null}
        {block.body ? (
          <div className="mt-1 text-[13px]">
            <Markdown content={block.body} />
          </div>
        ) : null}
        <Tags tags={block.tags} />
        {hasLink ? (
          web ? (
            <a
              href={url}
              target="_blank"
              rel="noreferrer noopener"
              className="mt-2 inline-block text-[12.5px] font-medium text-accent hover:underline"
            >
              Read Coverage →
            </a>
          ) : (
            <button
              type="button"
              onClick={onOpenDocument}
              className="mt-2 inline-block text-[12.5px] font-medium text-accent hover:underline"
            >
              Open document →
            </button>
          )
        ) : null}
      </div>
    </div>
  );
}

export function ArticleCard({
  block,
  onOpenDocument,
}: {
  block: ArticleCardBlockT;
  onOpenDocument?: () => void;
}) {
  if (block.layout === 'hero') {
    return <HeroCard block={block} onOpenDocument={onOpenDocument} />;
  }
  return <StandardCard block={block} onOpenDocument={onOpenDocument} />;
}
