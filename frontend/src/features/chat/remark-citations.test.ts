import { remark } from 'remark';

import { remarkCitations } from './remark-citations';

// remark is a transitive dep of react-markdown; add it explicitly for tests:
// pnpm add -D remark@^15.0.1

interface Node {
  type: string;
  value?: string;
  children?: Node[];
  data?: { hName?: string; hProperties?: { n?: string } };
}

function transform(md: string): Node {
  const processor = remark().use(remarkCitations);
  return processor.runSync(processor.parse(md)) as unknown as Node;
}

function flatten(node: Node, out: Node[] = []): Node[] {
  out.push(node);
  for (const child of node.children ?? []) flatten(child, out);
  return out;
}

test('splits [n] markers into citation nodes preserving surrounding text', () => {
  const nodes = flatten(transform('Revenue rose 12% [1] and churn fell [2].'));
  const chips = nodes.filter((n) => n.data?.hName === 'citation-chip');
  expect(chips.map((c) => c.data?.hProperties?.n)).toEqual(['1', '2']);
  const texts = nodes.filter((n) => n.type === 'text').map((n) => n.value);
  expect(texts).toEqual(['Revenue rose 12% ', ' and churn fell ', '.']);
});

test('leaves text without markers untouched', () => {
  const nodes = flatten(transform('No citations here [not one].'));
  expect(nodes.some((n) => n.data?.hName === 'citation-chip')).toBe(false);
});

test('does not rewrite inside inline code', () => {
  const nodes = flatten(transform('Use `arr[1]` to index.'));
  expect(nodes.some((n) => n.data?.hName === 'citation-chip')).toBe(false);
});
