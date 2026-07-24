import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import type { FolderOut } from '@/api/types';

import { buildFolderTree } from './folder-queries';
import { FolderTree } from './folder-tree';

function folder(over: Partial<FolderOut> & { id: string; name: string }): FolderOut {
  return {
    workspace_id: 'w1',
    parent_folder_id: null,
    created_at: '2026-07-18T00:00:00Z',
    ...over,
  };
}

test('buildFolderTree nests a 3-level flat list into a tree', () => {
  const flat: FolderOut[] = [
    folder({ id: 'grandchild', name: 'Grandchild', parent_folder_id: 'child' }),
    folder({ id: 'root-b', name: 'Root B' }),
    folder({ id: 'root-a', name: 'Root A' }),
    folder({ id: 'child', name: 'Child', parent_folder_id: 'root-a' }),
  ];

  const tree = buildFolderTree(flat);

  expect(tree.map((n) => n.id)).toEqual(['root-a', 'root-b']);
  const rootA = tree[0];
  expect(rootA?.children.map((n) => n.id)).toEqual(['child']);
  const child = rootA?.children[0];
  expect(child?.children.map((n) => n.id)).toEqual(['grandchild']);
  expect(child?.children[0]?.children).toEqual([]);
});

test('a folder whose parent is missing from the list is treated as a root', () => {
  const flat: FolderOut[] = [
    folder({ id: 'orphan', name: 'Orphan', parent_folder_id: 'missing-parent' }),
  ];

  const tree = buildFolderTree(flat);

  expect(tree.map((n) => n.id)).toEqual(['orphan']);
});

function buildTree() {
  return buildFolderTree([
    folder({ id: 'root-a', name: 'Root A' }),
    folder({ id: 'root-b', name: 'Root B' }),
    folder({ id: 'child', name: 'Child', parent_folder_id: 'root-a' }),
  ]);
}

// Default no-op handlers for the hover-action callbacks, spread into tests
// that don't exercise create/rename/delete so every render call stays short.
const noopActions = { onNewChild: vi.fn(), onRename: vi.fn(), onDelete: vi.fn() };

test('renders only "All documents" when there are zero folders (the default/common case)', () => {
  render(<FolderTree tree={[]} selectedId={null} onSelect={vi.fn()} {...noopActions} />);

  const nav = screen.getByRole('navigation', { name: 'Folders' });
  expect(within(nav).getByRole('button', { name: 'All documents' })).toBeInTheDocument();
  expect(within(nav).getAllByRole('button')).toHaveLength(1);
});

test('renders "All documents" plus every root folder (and its nested children)', () => {
  render(<FolderTree tree={buildTree()} selectedId={null} onSelect={vi.fn()} {...noopActions} />);

  expect(screen.getByRole('button', { name: 'All documents' })).toBeInTheDocument();
  expect(screen.getByText('Root A')).toBeInTheDocument();
  expect(screen.getByText('Root B')).toBeInTheDocument();
  expect(screen.getByText('Child')).toBeInTheDocument();
});

test('clicking a folder calls onSelect with its id', async () => {
  const user = userEvent.setup();
  const onSelect = vi.fn();
  render(<FolderTree tree={buildTree()} selectedId={null} onSelect={onSelect} {...noopActions} />);

  // Root B has no children, so its row is a plain button with an
  // unambiguous accessible name (no nested expand/collapse toggle).
  await user.click(screen.getByRole('button', { name: 'Root B' }));

  expect(onSelect).toHaveBeenCalledWith('root-b');
});

test('clicking a folder that has children also selects it (not just expand/collapse)', async () => {
  const user = userEvent.setup();
  const onSelect = vi.fn();
  render(<FolderTree tree={buildTree()} selectedId={null} onSelect={onSelect} {...noopActions} />);

  // Root A's row button has a nested toggle, which pollutes its computed
  // accessible name -- locate the row via its visible text instead.
  const rootARow = screen.getByText('Root A').closest('button');
  expect(rootARow).not.toBeNull();
  await user.click(rootARow as HTMLElement);

  expect(onSelect).toHaveBeenCalledWith('root-a');
});

test('clicking "All documents" calls onSelect(null)', async () => {
  const user = userEvent.setup();
  const onSelect = vi.fn();
  render(
    <FolderTree tree={buildTree()} selectedId="root-a" onSelect={onSelect} {...noopActions} />,
  );

  await user.click(screen.getByRole('button', { name: 'All documents' }));

  expect(onSelect).toHaveBeenCalledWith(null);
});

test('collapsing a folder hides its children without changing selection, and expanding restores them', async () => {
  const user = userEvent.setup();
  const onSelect = vi.fn();
  render(<FolderTree tree={buildTree()} selectedId={null} onSelect={onSelect} {...noopActions} />);

  expect(screen.getByText('Child')).toBeInTheDocument();

  await user.click(screen.getByRole('button', { name: 'Collapse Root A' }));

  expect(screen.queryByText('Child')).not.toBeInTheDocument();
  expect(onSelect).not.toHaveBeenCalled();

  await user.click(screen.getByRole('button', { name: 'Expand Root A' }));

  expect(screen.getByText('Child')).toBeInTheDocument();
  expect(onSelect).not.toHaveBeenCalled();
});

test('clicking "New folder" calls onNewChild(null) to create a root folder', async () => {
  const user = userEvent.setup();
  const onNewChild = vi.fn();
  render(
    <FolderTree
      tree={buildTree()}
      selectedId={null}
      onSelect={vi.fn()}
      {...noopActions}
      onNewChild={onNewChild}
    />,
  );

  await user.click(screen.getByRole('button', { name: 'New folder' }));

  expect(onNewChild).toHaveBeenCalledWith(null);
});

test('clicking a folder\'s "+" hover action calls onNewChild with that folder\'s id, not root', async () => {
  const user = userEvent.setup();
  const onNewChild = vi.fn();
  const onSelect = vi.fn();
  render(
    <FolderTree
      tree={buildTree()}
      selectedId={null}
      onSelect={onSelect}
      {...noopActions}
      onNewChild={onNewChild}
    />,
  );

  await user.click(screen.getByRole('button', { name: 'New subfolder in Root A' }));

  expect(onNewChild).toHaveBeenCalledWith('root-a');
  // The hover-action click must not also select the row.
  expect(onSelect).not.toHaveBeenCalled();
});

test("clicking a folder's rename hover action calls onRename with that folder node, without selecting it", async () => {
  const user = userEvent.setup();
  const onRename = vi.fn();
  const onSelect = vi.fn();
  render(
    <FolderTree
      tree={buildTree()}
      selectedId={null}
      onSelect={onSelect}
      {...noopActions}
      onRename={onRename}
    />,
  );

  await user.click(screen.getByRole('button', { name: 'Rename Root A' }));

  expect(onRename).toHaveBeenCalledTimes(1);
  expect(onRename.mock.calls[0]?.[0]?.id).toBe('root-a');
  expect(onSelect).not.toHaveBeenCalled();
});

test("clicking a folder's delete hover action calls onDelete with that folder node, without selecting it", async () => {
  const user = userEvent.setup();
  const onDelete = vi.fn();
  const onSelect = vi.fn();
  render(
    <FolderTree
      tree={buildTree()}
      selectedId={null}
      onSelect={onSelect}
      {...noopActions}
      onDelete={onDelete}
    />,
  );

  await user.click(screen.getByRole('button', { name: 'Delete Child' }));

  expect(onDelete).toHaveBeenCalledTimes(1);
  expect(onDelete.mock.calls[0]?.[0]?.id).toBe('child');
  expect(onSelect).not.toHaveBeenCalled();
});
