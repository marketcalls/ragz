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
import { StackedArea } from '@/components/charts/stacked-area';
import { ChartCard } from '@/components/charts/chart-card';
import { TimeSeriesLine } from '@/components/charts/time-series-line';
import { Markdown } from '@/components/markdown/markdown';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { cn } from '@/lib/cn';

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

// Maps a validated ChartBlock onto one of the Phase-1 chart primitives.
// Returns null whenever the block's data doesn't fit the chosen chart's
// required shape -- the caller renders nothing rather than crash or show a
// misleading "No data" placeholder for a fundamentally malformed block.
function renderChart(block: ChartBlockT): ReactNode | null {
  const { chart, data, x_key, category_key, keys } = block;

  switch (chart) {
    case 'donut': {
      const nameKey = category_key ?? x_key;
      const valueKey = keys?.[0];
      if (!nameKey || !valueKey || data.length === 0) return null;
      const points: { name: string; value: number }[] = [];
      for (const row of data) {
        const value = row[valueKey];
        if (!isFiniteNumber(value)) return null;
        points.push({ name: String(row[nameKey]), value });
      }
      return <DonutChart data={points} />;
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
    case 'radial_gauge': {
      const row = data[0];
      if (!row) return null;
      const valueKey = keys?.[0] ?? 'value';
      const maxKey = keys?.[1] ?? 'max';
      const value = row[valueKey];
      const max = maxKey in row ? row[maxKey] : 100;
      if (!isFiniteNumber(value) || !isFiniteNumber(max)) return null;
      return <RadialGauge value={value} max={max} label={block.title ?? undefined} />;
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
  return (
    <div className="rounded-lg border border-line bg-bg p-4">
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
    </div>
  );
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
        {block.items.map((item, i) => (
          <li key={i} className="flex items-start gap-3">
            <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-subtle text-[12px] font-medium tabular-nums text-secondary">
              {i + 1}
            </span>
            <div className="min-w-0">
              <p className="text-[13px] text-ink">{item.title}</p>
              {item.subtitle ? <p className="text-[12px] text-secondary">{item.subtitle}</p> : null}
            </div>
          </li>
        ))}
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

function TabsView({ block, depth }: { block: TabsBlockT; depth: number }) {
  // Backend types nesting out statically (TabItem.blocks excludes
  // TabsBlock), but this whitelist guards defensively anyway rather than
  // trust the server-validated shape blindly.
  if (depth > 0 || block.tabs.length === 0) return null;
  const firstLabel = block.tabs[0]?.label;
  return (
    <Tabs defaultValue={firstLabel} className="rounded-lg border border-line bg-bg p-3">
      <TabsList>
        {block.tabs.map((tab, i) => (
          <TabsTrigger key={`${tab.label}-${i}`} value={tab.label}>
            {tab.label}
          </TabsTrigger>
        ))}
      </TabsList>
      {block.tabs.map((tab, i) => (
        <TabsContent key={`${tab.label}-${i}`} value={tab.label} className="pt-3">
          <BlockRenderer blocks={tab.blocks} depth={depth + 1} />
        </TabsContent>
      ))}
    </Tabs>
  );
}

function RenderBlock({ block, depth }: { block: Block; depth: number }): ReactNode {
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
    case 'tag_badges':
      return <TagBadges block={block} />;
    case 'callout':
      return <CalloutView block={block} />;
    case 'table':
      return <TableView block={block} />;
    case 'tabs':
      return <TabsView block={block} depth={depth} />;
    default:
      // Unknown block type -- strict whitelist, render nothing.
      return null;
  }
}

export function BlockRenderer({ blocks, depth = 0 }: { blocks: Block[]; depth?: number }) {
  if (blocks.length === 0) return null;
  return (
    <div className="mt-3 flex flex-col gap-3">
      {blocks.map((block, i) => (
        <RenderBlock key={i} block={block} depth={depth} />
      ))}
    </div>
  );
}
