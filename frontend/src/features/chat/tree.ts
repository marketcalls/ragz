import type { MessageNode } from '@/api/types';

export const ROOT = '__root__';

export interface PathEntry {
  message: MessageNode;
  siblings: string[];
  position: number;
}

export type SelectionOverrides = Readonly<Record<string, string>>;

export function branchKeyOf(message: Pick<MessageNode, 'parent_message_id'>): string {
  return message.parent_message_id ?? ROOT;
}

/**
 * Nested ChatTreeOut.messages → the single rendered path (phase1 spec §2.1).
 * At each level: the override's child if it is one of this level's siblings,
 * else the NEWEST sibling. Sibling lists arrive sorted by sibling_index
 * (part of the GET /chats/{id} contract), so newest = last. Pure.
 */
export function selectActivePath(
  roots: readonly MessageNode[],
  overrides: SelectionOverrides,
): PathEntry[] {
  const path: PathEntry[] = [];
  let branchKey = ROOT;
  let siblings: readonly MessageNode[] = roots;
  while (siblings.length > 0) {
    const overrideId = overrides[branchKey];
    const chosen = siblings.find((m) => m.id === overrideId) ?? siblings[siblings.length - 1]!;
    path.push({
      message: chosen,
      siblings: siblings.map((m) => m.id),
      position: siblings.indexOf(chosen),
    });
    branchKey = chosen.id;
    siblings = chosen.children;
  }
  return path;
}

/** Depth-first membership test over the nested tree. */
export function treeContains(roots: readonly MessageNode[], id: string): boolean {
  for (const node of roots) {
    if (node.id === id || treeContains(node.children, id)) return true;
  }
  return false;
}
