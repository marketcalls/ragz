import type { MessageNode } from '@/api/types';

import { ROOT, branchKeyOf, selectActivePath, treeContains } from './tree';

function node(
  id: string,
  over: Partial<MessageNode> = {},
  children: MessageNode[] = [],
): MessageNode {
  return {
    id,
    parent_message_id: null,
    sibling_index: 0,
    role: 'user',
    content: `content-${id}`,
    model_id: null,
    prompt_tokens: null,
    completion_tokens: null,
    created_at: '2026-07-18T00:00:00Z',
    citations: [],
    children,
    ...over,
  } as MessageNode;
}

// u1 → a1 → u2 → a2 (nested, as GET /chats/{id} delivers it)
const linear = [
  node('u1', {}, [
    node('a1', { role: 'assistant', parent_message_id: 'u1' }, [
      node('u2', { parent_message_id: 'a1' }, [
        node('a2', { role: 'assistant', parent_message_id: 'u2' }),
      ]),
    ]),
  ]),
];

// same thread after editing u2: a1 has children [u2 → a2, u2b → a2b] (sorted by sibling_index)
const edited = [
  node('u1', {}, [
    node('a1', { role: 'assistant', parent_message_id: 'u1' }, [
      node('u2', { parent_message_id: 'a1' }, [
        node('a2', { role: 'assistant', parent_message_id: 'u2' }),
      ]),
      node('u2b', { parent_message_id: 'a1', sibling_index: 1 }, [
        node('a2b', { role: 'assistant', parent_message_id: 'u2b' }),
      ]),
    ]),
  ]),
];

test('linear thread returns the full path with singleton sibling sets', () => {
  const path = selectActivePath(linear, {});
  expect(path.map((p) => p.message.id)).toEqual(['u1', 'a1', 'u2', 'a2']);
  expect(path.every((p) => p.siblings.length === 1 && p.position === 0)).toBe(true);
});

test('edited user message: newest sibling wins by default, old downstream kept apart', () => {
  const path = selectActivePath(edited, {});
  expect(path.map((p) => p.message.id)).toEqual(['u1', 'a1', 'u2b', 'a2b']);
  const entry = path[2]!;
  expect(entry.siblings).toEqual(['u2', 'u2b']);
  expect(entry.position).toBe(1); // renders as 2/2
});

test('override navigates back to the older sibling and its own answers', () => {
  const path = selectActivePath(edited, { a1: 'u2' });
  expect(path.map((p) => p.message.id)).toEqual(['u1', 'a1', 'u2', 'a2']);
  expect(path[2]!.position).toBe(0); // renders as 1/2
});

test('regenerated assistant: newest sibling default, both reachable', () => {
  const regen = [
    node('u1', {}, [
      node('a1', { role: 'assistant', parent_message_id: 'u1' }),
      node('a1b', { role: 'assistant', parent_message_id: 'u1', sibling_index: 1 }),
    ]),
  ];
  expect(selectActivePath(regen, {}).at(-1)!.message.id).toBe('a1b');
  expect(selectActivePath(regen, { u1: 'a1' }).at(-1)!.message.id).toBe('a1');
});

test('invalid override id falls back to newest', () => {
  const roots = [node('u1'), node('u1b', { sibling_index: 1 })];
  const path = selectActivePath(roots, { [ROOT]: 'nope' });
  expect(path[0]!.message.id).toBe('u1b');
});

test('empty tree is safe', () => {
  expect(selectActivePath([], {})).toEqual([]);
});

test('treeContains finds deep ids and rejects missing ones', () => {
  expect(treeContains(edited, 'a2b')).toBe(true);
  expect(treeContains(edited, 'u1')).toBe(true);
  expect(treeContains(edited, 'ghost')).toBe(false);
  expect(treeContains([], 'u1')).toBe(false);
});

test('branchKeyOf', () => {
  expect(branchKeyOf(node('u1'))).toBe(ROOT);
  expect(branchKeyOf(node('a1', { parent_message_id: 'u1' }))).toBe('u1');
});
