import { act, renderHook } from '@testing-library/react';

import type { PendingAttachment } from './use-pending-attachments';
import { useSendMessage } from './use-send-message';

const uploadPendingAttachments = vi.fn();
vi.mock('./queries', () => ({
  uploadPendingAttachments: (...args: unknown[]) => uploadPendingAttachments(...args),
}));

function pendingFile(name: string): PendingAttachment {
  return { id: name, file: new File(['x'], name, { type: 'text/plain' }), previewUrl: null };
}

afterEach(() => {
  uploadPendingAttachments.mockReset();
});

test('text-only send on an existing chat sends directly with no attachment upload', async () => {
  const sendToChat = vi.fn();
  const createChat = vi.fn();
  const clearPending = vi.fn();
  const { result } = renderHook(() =>
    useSendMessage({
      chatId: 'chat-1',
      workspaceId: 'ws-1',
      createChat,
      sendToChat,
      onNewChat: vi.fn(),
      pendingFiles: [],
      clearPending,
    }),
  );

  await act(async () => result.current.send('hello'));

  expect(createChat).not.toHaveBeenCalled();
  expect(uploadPendingAttachments).not.toHaveBeenCalled();
  expect(sendToChat).toHaveBeenCalledWith('hello', undefined, []);
  expect(clearPending).not.toHaveBeenCalled();
  expect(result.current.error).toBeNull();
});

test('sending with pending files on an existing chat uploads then sends with the returned ids', async () => {
  uploadPendingAttachments.mockResolvedValue(['att-1', 'att-2']);
  const sendToChat = vi.fn();
  const clearPending = vi.fn();
  const { result } = renderHook(() =>
    useSendMessage({
      chatId: 'chat-1',
      workspaceId: 'ws-1',
      createChat: vi.fn(),
      sendToChat,
      onNewChat: vi.fn(),
      pendingFiles: [pendingFile('a.txt'), pendingFile('b.txt')],
      clearPending,
    }),
  );

  await act(async () => result.current.send('hello'));

  expect(uploadPendingAttachments).toHaveBeenCalledWith('chat-1', [
    expect.objectContaining({ name: 'a.txt' }),
    expect.objectContaining({ name: 'b.txt' }),
  ]);
  expect(sendToChat).toHaveBeenCalledWith('hello', undefined, ['att-1', 'att-2']);
  expect(clearPending).toHaveBeenCalled();
});

test('new-chat path: the chat is created first, then uploads target the real chat id (never null)', async () => {
  uploadPendingAttachments.mockResolvedValue(['att-9']);
  const createChat = vi.fn().mockResolvedValue({ id: 'new-chat-id' });
  const onNewChat = vi.fn();
  const clearPending = vi.fn();
  const { result } = renderHook(() =>
    useSendMessage({
      chatId: null,
      workspaceId: 'ws-1',
      createChat,
      sendToChat: vi.fn(),
      onNewChat,
      pendingFiles: [pendingFile('photo.png')],
      clearPending,
    }),
  );

  await act(async () => result.current.send('first message'));

  expect(createChat).toHaveBeenCalledWith({ workspace_id: 'ws-1' });
  expect(uploadPendingAttachments).toHaveBeenCalledWith('new-chat-id', [
    expect.objectContaining({ name: 'photo.png' }),
  ]);
  // Never the broken pre-fix behavior of uploading against a null chat id.
  expect(uploadPendingAttachments).not.toHaveBeenCalledWith(null, expect.anything());
  expect(onNewChat).toHaveBeenCalledWith('new-chat-id', 'first message', ['att-9']);
  expect(clearPending).toHaveBeenCalled();
});

test('a new chat with no pending files persists the message via first_message and hands off nothing', async () => {
  const createChat = vi.fn().mockResolvedValue({ id: 'new-chat-id' });
  const onNewChat = vi.fn();
  const { result } = renderHook(() =>
    useSendMessage({
      chatId: null,
      workspaceId: 'ws-1',
      createChat,
      sendToChat: vi.fn(),
      onNewChat,
      pendingFiles: [],
      clearPending: vi.fn(),
    }),
  );

  await act(async () => result.current.send('hi'));

  expect(uploadPendingAttachments).not.toHaveBeenCalled();
  // The message rides along with the chat creation, so it is durable the
  // instant the chat exists...
  expect(createChat).toHaveBeenCalledWith({ workspace_id: 'ws-1', first_message: 'hi' });
  // ...and nothing is handed through browser state for a reload to lose.
  expect(onNewChat).toHaveBeenCalledWith('new-chat-id', null, []);
});

test('an upload failure on send surfaces an error, keeps pending files, and does not send', async () => {
  uploadPendingAttachments.mockRejectedValue(new Error('Failed to attach "a.txt". Please try again.'));
  const sendToChat = vi.fn();
  const clearPending = vi.fn();
  const { result } = renderHook(() =>
    useSendMessage({
      chatId: 'chat-1',
      workspaceId: 'ws-1',
      createChat: vi.fn(),
      sendToChat,
      onNewChat: vi.fn(),
      pendingFiles: [pendingFile('a.txt')],
      clearPending,
    }),
  );

  await act(async () => result.current.send('hello'));

  expect(sendToChat).not.toHaveBeenCalled();
  expect(clearPending).not.toHaveBeenCalled();
  expect(result.current.error).toBe('Failed to attach "a.txt". Please try again.');
  expect(result.current.sending).toBe(false);
});

test('a first message before the workspace resolves surfaces an error, never a silent no-op', async () => {
  const createChat = vi.fn();
  const onNewChat = vi.fn();
  const { result } = renderHook(() =>
    useSendMessage({
      chatId: null,
      workspaceId: null, // workspace context not yet resolved
      createChat,
      sendToChat: vi.fn(),
      onNewChat,
      pendingFiles: [],
      clearPending: vi.fn(),
    }),
  );

  await act(async () => result.current.send('first message'));

  // Nothing is sent, but the user is TOLD why -- not left staring at a chat
  // where their message silently never appeared (bug report 2026-08-15).
  expect(createChat).not.toHaveBeenCalled();
  expect(onNewChat).not.toHaveBeenCalled();
  expect(result.current.error).toContain('workspace');
  expect(result.current.sending).toBe(false);
});

test('a chat-creation failure surfaces an error and never attempts an upload', async () => {
  const createChat = vi.fn().mockRejectedValue(new Error('boom'));
  const { result } = renderHook(() =>
    useSendMessage({
      chatId: null,
      workspaceId: 'ws-1',
      createChat,
      sendToChat: vi.fn(),
      onNewChat: vi.fn(),
      pendingFiles: [pendingFile('a.txt')],
      clearPending: vi.fn(),
    }),
  );

  await act(async () => result.current.send('hello'));

  expect(uploadPendingAttachments).not.toHaveBeenCalled();
  expect(result.current.error).toContain('new chat');
});
