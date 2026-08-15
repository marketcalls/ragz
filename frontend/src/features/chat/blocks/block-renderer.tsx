import {
  AlertTriangle,
  BarChart3,
  Calendar,
  Check,
  Clock,
  DollarSign,
  FileText,
  Globe,
  Info,
  Shield,
  Sparkles,
  Star,
  Target,
  Trophy,
  Users,
  type LucideIcon,
} from 'lucide-react';
import type { ReactNode } from 'react';

import type {
  Block,
  CalloutBlock as CalloutBlockT,
  ChartBlock as ChartBlockT,
  ImageCardBlock as ImageCardBlockT,
  InfoCardBlock as InfoCardBlockT,
  RankedListBlock as RankedListBlockT,
  TableBlock as TableBlockT,
  TabsBlock as TabsBlockT,
  TagBadgesBlock as TagBadgesBlockT,
} from '@/api/types';
import { DonutChart } from '@/components/charts/donut-chart';
import { GroupedBar } from '@/components/charts/grouped-bar';
import { RadarChart } from '@/components/charts/radar-chart';
import { RadialGauge } from '@/components/charts/radial-gauge';
import { ScatterChart } from '@/components/charts/scatter-chart';
import { SingleStackedBar } from '@/components/charts/single-stacked-bar';
import { Sparkline } from '@/components/charts/sparkline';
import { StackedArea } from '@/components/charts/stacked-area';
import { StackedBars } from '@/components/charts/stacked-bars';
import { ChartCard } from '@/components/charts/chart-card';
import { TimeSeriesLine } from '@/components/charts/time-series-line';
import { Markdown } from '@/components/markdown/markdown';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { cn } from '@/lib/cn';

import { Accordion } from './accordion';
import { ArticleCard } from './article-card';
import { FollowUps } from './follow-ups';
import { FormBlockView } from './form-block';
import { SourceRefsView } from './source-refs';
import type { SourceChipData } from '../source-panel';

// Iron Rule 5: block payloads come from an LLM (server-validated already,
// see backend chat/blocks.py) but are still treated as hostile here. This
// module is a strict WHITELIST -- a `switch (block.type)` that renders
// nothing for anything it doesn't explicitly recognize, never
// dangerouslySetInnerHTML with model text, and never loads a model-supplied
// URL (see ImageCard below: image_ref is opaque, never loaded as a src).

// Named icons only -- never a model-supplied SVG/icon string. Keys mirror
// the backend's whitelisted IconName (chat/blocks.py).
const ICONS: Record<string, LucideIcon> = {
  info: Info,
  chart: BarChart3,
  dollar: DollarSign,
  trophy: Trophy,
  warning: AlertTriangle,
  doc: FileText,
  spark: Sparkles,
  users: Users,
  clock: Clock,
  check: Check,
  star: Star,
  target: Target,
  globe: Globe,
  shield: Shield,
  calendar: Calendar,
};

const TAG_TONES: Record<string, string> = {
  neutral: 'bg-subtle text-secondary',
  info: 'bg-accent-soft text-accent-on-soft',
  success: 'bg-success-soft text-success',
  warning: 'bg-warning-soft text-warning',
  danger: 'bg-danger-soft text-danger',
};

const CALLOUT_TONES: Record<string, string> = {
  info: 'border-accent bg-accent-soft text-accent-on-soft',
  success: 'border-success bg-success-soft text-success',
  warning: 'border-warning bg-warning-soft text-warning',
  danger: 'border-danger bg-danger-soft text-danger',
};

function isFiniteNumber(v: unknown): v is number {
  return typeof v === 'number' && Number.isFinite(v);
}

// Same http(s)-only guard used across the block renderers (copied from
// behind-the-scenes.tsx -- see source-refs.tsx/article-card.tsx for the
// other copies). A `url` field is already validated server-side, but this
// module never trusts a model-authored field without re-checking here too.
function isHttpUrl(url: string | null | undefined): url is string {
  if (!url) return false;
  try {
    const parsed = new URL(url);
    return parsed.protocol === 'http:' || parsed.protocol === 'https:';
  } catch {
    return false;
  }
}

// Builds the SourcePanel chip shape for a source_refs item or an
// article_card block's own document_id/page/title -- both open the same
// in-app document viewer via onOpenDocument. marker/snippet/section/version
// carry no meaning for a generative-UI block click (there's no citation
// marker), so they're set to inert defaults; only document_id/page/filename
// drive the viewer.
function toChip(source: { title: string; document_id?: string | null; page?: number | null }): SourceChipData {
  return {
    marker: 0,
    document_id: source.document_id ?? '',
    filename: source.title,
    page: source.page ?? 0,
    snippet: '',
    section: null,
    version: 0,
    url: null,
  };
}

// Shared by 'donut'/'pie': both need a { name, value }[] series keyed off
// category_key/x_key (the label) and keys[0] (the numeric value). Returns
// null on any non-numeric value, same "malformed -> render nothing" contract
// as every other case below.
function toCategoryValuePoints(
  data: ChartBlockT['data'],
  nameKey: string | null | undefined,
  valueKey: string | null | undefined,
): { name: string; value: number }[] | null {
  if (!nameKey || !valueKey || data.length === 0) return null;
  const points: { name: string; value: number }[] = [];
  for (const row of data) {
    const value = row[valueKey];
    if (!isFiniteNumber(value)) return null;
    points.push({ name: String(row[nameKey]), value });
  }
  return points;
}

// Shared by 'radial_gauge'/'semi_gauge': both read a single row's value/max
// (keys[0]/keys[1], defaulting to 'value'/'max' fields, max defaulting to
// 100 when absent).
function toGaugeValue(
  data: ChartBlockT['data'],
  keys: string[] | null | undefined,
): { value: number; max: number } | null {
  const row = data[0];
  if (!row) return null;
  const valueKey = keys?.[0] ?? 'value';
  const maxKey = keys?.[1] ?? 'max';
  const value = row[valueKey];
  const max = maxKey in row ? row[maxKey] : 100;
  if (!isFiniteNumber(value) || !isFiniteNumber(max)) return null;
  return { value, max };
}

// Maps a validated ChartBlock onto one of the chart primitives. Returns null
// whenever the block's data doesn't fit the chosen chart's required shape --
// the caller renders nothing rather than crash or show a misleading "No
// data" placeholder for a fundamentally malformed block.
function renderChart(block: ChartBlockT): ReactNode | null {
  const { chart, data, x_key, category_key, keys } = block;

  switch (chart) {
    case 'donut': {
      const points = toCategoryValuePoints(data, category_key ?? x_key, keys?.[0]);
      if (!points) return null;
      return <DonutChart data={points} />;
    }
    case 'pie': {
      const points = toCategoryValuePoints(data, category_key ?? x_key, keys?.[0]);
      if (!points) return null;
      return <DonutChart data={points} variant="pie" />;
    }
    case 'radar': {
      const categoryKey = category_key ?? x_key;
      if (!categoryKey || !keys || keys.length === 0 || data.length === 0) return null;
      return <RadarChart data={data} categoryKey={categoryKey} keys={keys} />;
    }
    case 'stacked_area':
    case 'area': {
      const xKey = x_key ?? category_key;
      if (!xKey || !keys || keys.length === 0 || data.length === 0) return null;
      return <StackedArea data={data} xKey={xKey} keys={keys} />;
    }
    case 'grouped_bar':
    case 'bar': {
      const categoryKey = category_key ?? x_key;
      if (!categoryKey || !keys || keys.length === 0 || data.length === 0) return null;
      return <GroupedBar data={data} categoryKey={categoryKey} keys={keys} />;
    }
    case 'horizontal_bar': {
      const categoryKey = category_key ?? x_key;
      if (!categoryKey || !keys || keys.length === 0 || data.length === 0) return null;
      return <GroupedBar data={data} categoryKey={categoryKey} keys={keys} horizontal />;
    }
    case 'stacked_bars': {
      const xKey = x_key ?? category_key;
      if (!xKey || !keys || keys.length === 0 || data.length === 0) return null;
      return <StackedBars data={data} xKey={xKey} keys={keys} />;
    }
    case 'single_stacked_bar': {
      const row = data[0];
      if (!row || !keys || keys.length === 0) return null;
      const values: Record<string, number> = {};
      for (const k of keys) {
        const v = row[k];
        if (!isFiniteNumber(v)) return null;
        values[k] = v;
      }
      return <SingleStackedBar data={values} keys={keys} />;
    }
    case 'sparkline': {
      const valueKey = keys?.[0];
      if (!valueKey || data.length === 0) return null;
      const points: number[] = [];
      for (const row of data) {
        const value = row[valueKey];
        if (!isFiniteNumber(value)) return null;
        points.push(value);
      }
      return <Sparkline data={points} />;
    }
    case 'scatter': {
      const yKey = keys?.[0];
      const zKey = keys?.[1];
      if (!x_key || !yKey || data.length === 0) return null;
      for (const row of data) {
        if (!isFiniteNumber(row[x_key]) || !isFiniteNumber(row[yKey])) return null;
        if (zKey && !isFiniteNumber(row[zKey])) return null;
      }
      return <ScatterChart data={data} xKey={x_key} yKey={yKey} zKey={zKey} />;
    }
    case 'radial_gauge': {
      const gauge = toGaugeValue(data, keys);
      if (!gauge) return null;
      return <RadialGauge value={gauge.value} max={gauge.max} label={block.title ?? undefined} />;
    }
    case 'semi_gauge': {
      const gauge = toGaugeValue(data, keys);
      if (!gauge) return null;
      return (
        <RadialGauge value={gauge.value} max={gauge.max} label={block.title ?? undefined} variant="semi" />
      );
    }
    case 'line': {
      const xKey = x_key ?? category_key;
      const valueKey = keys?.[0];
      if (!xKey || !valueKey || data.length === 0) return null;
      const points: { day: string; count: number }[] = [];
      for (const row of data) {
        const count = row[valueKey];
        if (!isFiniteNumber(count)) return null;
        points.push({ day: String(row[xKey]), count });
      }
      return <TimeSeriesLine data={points} />;
    }
    default:
      return null;
  }
}

function ChartBlockView({ block }: { block: ChartBlockT }) {
  const chart = renderChart(block);
  if (!chart) return null;
  return (
    <ChartCard title={block.title ?? undefined} subtitle={block.subtitle ?? undefined}>
      {chart}
    </ChartCard>
  );
}

function InfoCard({ block }: { block: InfoCardBlockT }) {
  const Icon = block.icon ? ICONS[block.icon] : null;
  const inner = (
    <div className="flex items-start gap-3">
      {Icon ? (
        <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-accent-soft text-accent-on-soft">
          <Icon className="h-4 w-4" aria-hidden />
        </span>
      ) : null}
      <div className="min-w-0 flex-1">
        <h3 className="text-sm font-semibold text-ink">{block.title}</h3>
        {block.subtitle ? <p className="mt-0.5 text-[12px] text-secondary">{block.subtitle}</p> : null}
        {block.body ? (
          <div className="mt-1">
            <Markdown content={block.body} />
          </div>
        ) : null}
      </div>
    </div>
  );
  // Design 2026-08-16 (openui parity, §3.1): an optional http(s) `url` makes
  // the whole card an external link; anything else (absent, non-http) keeps
  // the card as inert display -- never a javascript:/data: href.
  if (isHttpUrl(block.url)) {
    return (
      <a
        href={block.url}
        target="_blank"
        rel="noreferrer noopener"
        className="block rounded-lg border border-line bg-bg p-4 hover:border-accent"
      >
        {inner}
      </a>
    );
  }
  return <div className="rounded-lg border border-line bg-bg p-4">{inner}</div>;
}

// DELIBERATELY renders no <img>: image_ref is an opaque, model-supplied id,
// never a URL, and there is no image-serving endpoint yet (see design doc
// task brief). A gradient placeholder header stands in for the image.
function ImageCard({ block }: { block: ImageCardBlockT }) {
  return (
    <div className="overflow-hidden rounded-lg border border-line bg-bg">
      <div className="h-20 bg-accent-soft" aria-hidden />
      <div className="p-3">
        {block.badge ? (
          <span className="mb-1.5 inline-flex items-center rounded-full bg-subtle px-2 py-0.5 text-[11px] font-medium text-secondary">
            {block.badge}
          </span>
        ) : null}
        <h3 className="text-sm font-semibold text-ink">{block.title}</h3>
        {block.subtitle ? <p className="mt-0.5 text-[12px] text-secondary">{block.subtitle}</p> : null}
      </div>
    </div>
  );
}

function RankedList({ block }: { block: RankedListBlockT }) {
  return (
    <div className="rounded-lg border border-line bg-bg p-4">
      {block.title ? <h3 className="mb-3 text-sm font-semibold text-ink">{block.title}</h3> : null}
      <ol className="flex flex-col gap-2.5">
        {block.items.map((item, i) => {
          const inner = (
            <>
              <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-subtle text-[12px] font-medium tabular-nums text-secondary">
                {i + 1}
              </span>
              <div className="min-w-0">
                <p className="text-[13px] text-ink">{item.title}</p>
                {item.subtitle ? <p className="text-[12px] text-secondary">{item.subtitle}</p> : null}
              </div>
            </>
          );
          // Same http(s)-only guard as InfoCard/ArticleCard: an optional
          // `url` makes the row a clickable external link.
          if (isHttpUrl(item.url)) {
            return (
              <li key={i}>
                <a
                  href={item.url}
                  target="_blank"
                  rel="noreferrer noopener"
                  className="flex items-start gap-3 hover:opacity-80"
                >
                  {inner}
                </a>
              </li>
            );
          }
          return (
            <li key={i} className="flex items-start gap-3">
              {inner}
            </li>
          );
        })}
      </ol>
    </div>
  );
}

function TagBadges({ block }: { block: TagBadgesBlockT }) {
  return (
    <div className="flex flex-wrap gap-1.5">
      {block.tags.map((tag, i) => (
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

function CalloutView({ block }: { block: CalloutBlockT }) {
  return (
    <div
      className={cn('rounded-md border-l-4 px-4 py-3', CALLOUT_TONES[block.tone] ?? CALLOUT_TONES.info)}
    >
      {block.title ? <p className="text-[13px] font-semibold">{block.title}</p> : null}
      <div className="text-[13px]">
        <Markdown content={block.body} />
      </div>
    </div>
  );
}

function TableView({ block }: { block: TableBlockT }) {
  return (
    <div className="overflow-x-auto rounded-lg border border-line">
      <table className="w-full text-[13px]">
        <thead className="bg-raised text-left">
          <tr>
            {block.columns.map((col, i) => (
              <th key={i} className="border-b border-line px-2.5 py-1.5 font-medium text-secondary">
                {col}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {block.rows.map((row, i) => (
            <tr key={i} className="even:bg-raised">
              {row.map((cell, j) => (
                <td key={j} className="border-b border-line-faint px-2.5 py-1.5 text-ink">
                  {String(cell)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function TabsView({
  block,
  depth,
  onFormSubmit,
  onFollowUp,
  onOpenDocument,
}: {
  block: TabsBlockT;
  depth: number;
  onFormSubmit?: (message: string) => void;
  onFollowUp?: (message: string) => void;
  onOpenDocument?: (source: SourceChipData) => void;
}) {
  // Backend types nesting out statically (TabItem.blocks excludes
  // TabsBlock), but this whitelist guards defensively anyway rather than
  // trust the server-validated shape blindly.
  if (depth > 0 || block.tabs.length === 0) return null;
  // Use the index as the Radix value/key: model-supplied labels can collide
  // (duplicate labels would break tab selection) — the index is always unique.
  return (
    <Tabs defaultValue="0" className="rounded-lg border border-line bg-bg p-3">
      <TabsList>
        {block.tabs.map((tab, i) => (
          <TabsTrigger key={i} value={String(i)}>
            {tab.label}
          </TabsTrigger>
        ))}
      </TabsList>
      {block.tabs.map((tab, i) => (
        <TabsContent key={i} value={String(i)} className="pt-3">
          <BlockRenderer
            blocks={tab.blocks}
            depth={depth + 1}
            onFormSubmit={onFormSubmit}
            onFollowUp={onFollowUp}
            onOpenDocument={onOpenDocument}
          />
        </TabsContent>
      ))}
    </Tabs>
  );
}

function RenderBlock({
  block,
  depth,
  onFormSubmit,
  onFollowUp,
  onOpenDocument,
}: {
  block: Block;
  depth: number;
  onFormSubmit?: (message: string) => void;
  onFollowUp?: (message: string) => void;
  onOpenDocument?: (source: SourceChipData) => void;
}): ReactNode {
  switch (block.type) {
    case 'text':
      return <Markdown content={block.markdown} />;
    case 'chart':
      return <ChartBlockView block={block} />;
    case 'info_card':
      return <InfoCard block={block} />;
    case 'image_card':
      return <ImageCard block={block} />;
    case 'ranked_list':
      return <RankedList block={block} />;
    case 'source_refs':
      return (
        <SourceRefsView
          block={block}
          onOpenDocument={onOpenDocument ? (item) => onOpenDocument(toChip(item)) : undefined}
        />
      );
    case 'tag_badges':
      return <TagBadges block={block} />;
    case 'article_card':
      return (
        <ArticleCard
          block={block}
          onOpenDocument={onOpenDocument ? () => onOpenDocument(toChip(block)) : undefined}
        />
      );
    case 'callout':
      return <CalloutView block={block} />;
    case 'table':
      return <TableView block={block} />;
    case 'tabs':
      return (
        <TabsView
          block={block}
          depth={depth}
          onFormSubmit={onFormSubmit}
          onFollowUp={onFollowUp}
          onOpenDocument={onOpenDocument}
        />
      );
    case 'form':
      return <FormBlockView block={block} onSubmit={onFormSubmit} />;
    case 'follow_ups':
      return <FollowUps block={block} onFollowUp={onFollowUp} />;
    case 'accordion':
      return (
        <Accordion
          block={block}
          depth={depth}
          onFormSubmit={onFormSubmit}
          onFollowUp={onFollowUp}
          onOpenDocument={onOpenDocument}
        />
      );
    default:
      // Unknown block type -- strict whitelist, render nothing.
      return null;
  }
}

export function BlockRenderer({
  blocks,
  depth = 0,
  onFormSubmit,
  onFollowUp,
  onOpenDocument,
}: {
  blocks: Block[];
  depth?: number;
  // Phase 3: a `form` block's submit composes a normal chat message and
  // sends it through the SAME send path as the composer (wired from
  // chat-page.tsx down through AssistantMessage). Absent in contexts with
  // no send path -- the form still renders but submit is a disabled no-op.
  onFormSubmit?: (message: string) => void;
  // Task 6 (openui-parity design 2026-08-16, §6): a `follow_ups` chip's click
  // sends its text as a normal chat message through the SAME send path as
  // onFormSubmit (wired from chat-page.tsx's onSend, exactly mirroring
  // onFormSubmit's wiring). Absent -- the chips still render but disabled.
  onFollowUp?: (message: string) => void;
  // openui-parity design (2026-08-16, Task 4): source_refs/article_card doc
  // items open the SAME in-app document viewer as citation chips
  // (source-panel.tsx's SourceChipData). Absent in contexts with no viewer
  // -- the button/card still renders but the click is a no-op.
  onOpenDocument?: (source: SourceChipData) => void;
}) {
  if (blocks.length === 0) return null;
  return (
    <div className="mt-3 flex flex-col gap-3">
      {blocks.map((block, i) => (
        <RenderBlock
          key={i}
          block={block}
          depth={depth}
          onFormSubmit={onFormSubmit}
          onFollowUp={onFollowUp}
          onOpenDocument={onOpenDocument}
        />
      ))}
    </div>
  );
}
