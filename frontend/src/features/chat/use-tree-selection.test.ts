import { act, renderHook } from '@testing-library/react';

import type { MessageNode } from '@/api/types';

import { useTreeSelection } from './use-tree-selection';

const base = {
  parent_message_id: null,
  role: 'user',
  model_id: null,
  prompt_tokens: null,
  completion_tokens: null,
  created_at: 't1',
  citations: [],
  children: [],
  stopped: false,
  no_answer: false,
  grounding: 'documents',
};
const roots = [
  { ...base, id: 'u1', sibling_index: 0, content: 'q' },
  { ...base, id: 'u1b', sibling_index: 1, content: 'q2' },
] as MessageNode[];

test('defaults to newest, select() navigates a branch', () => {
  const { result, rerender } = renderHook(({ msgs }) => useTreeSelection(msgs), {
    initialProps: { msgs: roots },
  });
  expect(result.current.path[0]?.message.id).toBe('u1b');
  act(() => result.current.select('__root__', 'u1'));
  rerender({ msgs: roots });
  expect(result.current.path[0]?.message.id).toBe('u1');
  expect(result.current.path[0]?.position).toBe(0);
  expect(result.current.path[0]?.siblings).toHaveLength(2);
});
