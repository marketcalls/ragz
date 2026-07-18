import { visit, SKIP } from 'unist-util-visit';

interface TextNode {
  type: 'text';
  value: string;
}

interface Parent {
  type: string;
  children: Array<Record<string, unknown>>;
}

const MARKER = /\[(\d{1,2})\]/g;

/** remark plugin: turn `[n]` in prose text into citation-chip nodes. */
export function remarkCitations() {
  return (tree: Parent): void => {
    visit(
      tree as never,
      'text',
      (node: TextNode, index: number | undefined, parent: Parent | undefined) => {
        if (!parent || index === undefined) return;
        if (parent.type === 'code' || parent.type === 'inlineCode' || parent.type === 'link') return;
        MARKER.lastIndex = 0;
        if (!MARKER.test(node.value)) return;
        MARKER.lastIndex = 0;

        const replacement: Array<Record<string, unknown>> = [];
        let cursor = 0;
        let match: RegExpExecArray | null;
        while ((match = MARKER.exec(node.value)) !== null) {
          if (match.index > cursor) {
            replacement.push({ type: 'text', value: node.value.slice(cursor, match.index) });
          }
          replacement.push({
            type: 'citationChip',
            data: { hName: 'citation-chip', hProperties: { n: match[1] } },
            children: [],
          });
          cursor = match.index + match[0].length;
        }
        if (cursor < node.value.length) {
          replacement.push({ type: 'text', value: node.value.slice(cursor) });
        }
        parent.children.splice(index, 1, ...replacement);
        return [SKIP, index + replacement.length] as const;
      },
    );
  };
}
