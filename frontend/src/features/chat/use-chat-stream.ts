import { useQueryClient } from '@tanstack/react-query';
import { useCallback, useRef, useState } from 'react';

import type { AgentStepInfo, Block, CitationRef, SourceRef, ToolResultInfo } from '@/api/types';

import { streamChatSse, type ChatSseEvent } from './stream';

export type StreamStatus = 'idle' | 'retrieving' | 'streaming' | 'done' | 'error';

export interface ChatStreamState {
  status: StreamStatus;
  text: string;
  sources: SourceRef[];
  citations: CitationRef[];
  // Phase 2 (in-chat generative UI): captured off the `blocks` SSE frame,
  // same shape as persisted MessageNode.blocks -- both feed BlockRenderer.
  blocks: Block[];
  noAnswer: boolean;
  grounding: 'documents' | 'general';
  // Phase 3 Plan J (design D4/§3): true only when a strict_mode workspace's
  // Gatekeeper regenerated once and never re-verified the retry.
  validationFailed: boolean;
  errorDetail: string | null;
  pendingUserContent: string | null;
  doneMessageId: string | null; // lets the page hide the streamed block once the refetched tree contains it
  agentSteps: AgentStepInfo[]; // Phase 3 (Task 10): progress line on escalated turns
  // Design 2026-08-15 ("Behind the scenes" UI): captured off `tool_result`
  // frames, keyed by step `n` to pair with agentSteps. Live-turn-only, same
  // as agentSteps -- neither is persisted, so this resets on every send.
  toolResults: ToolResultInfo[];
}

const IDLE: ChatStreamState = {
  status: 'idle',
  text: '',
  sources: [],
  citations: [],
  blocks: [],
  noAnswer: false,
  grounding: 'documents',
  validationFailed: false,
  errorDetail: null,
  pendingUserContent: null,
  doneMessageId: null,
  agentSteps: [],
  toolResults: [],
};

function reduce(state: ChatStreamState, event: ChatSseEvent): ChatStreamState {
  switch (event.type) {
    case 'retrieval_started':
      return { ...state, status: 'retrieving' };
    case 'sources':
      return { ...state, sources: event.sources };
    case 'token':
      return { ...state, status: 'streaming', text: state.text + event.delta };
    case 'citations':
      return { ...state, citations: event.citations };
    case 'blocks':
      return { ...state, blocks: event.blocks };
    case 'done':
      return {
        ...state,
        status: 'done',
        noAnswer: event.done.no_answer,
        grounding: event.done.grounding,
        validationFailed: event.done.validation_failed,
        doneMessageId: event.done.message_id,
      };
    case 'error':
      return { ...state, status: 'error', errorDetail: event.detail };
    case 'agent_step':
      return { ...state, status: 'retrieving', agentSteps: [...state.agentSteps, event.step] };
    case 'tool_result':
      return { ...state, toolResults: [...state.toolResults, event.result] };
  }
}

export function useChatStream(chatId: string | null) {
  const queryClient = useQueryClient();
  const [state, setState] = useState<ChatStreamState>(IDLE);
  const abortRef = useRef<AbortController | null>(null);

  const run = useCallback(
    (url: string, body: unknown, pendingUserContent: string | null) => {
      abortRef.current?.abort();
      const controller = new AbortController();
      abortRef.current = controller;
      setState({ ...IDLE, status: 'retrieving', pendingUserContent });
      void streamChatSse(
        url,
        body,
        (event) => {
          setState((prev) => reduce(prev, event));
          if (event.type === 'done') {
            void queryClient.invalidateQueries({ queryKey: ['chat', chatId] });
            void queryClient.invalidateQueries({ queryKey: ['chats'] });
          }
        },
        controller.signal,
      );
    },
    [chatId, queryClient],
  );

  const send = useCallback(
    (
      content: string,
      parentMessageId?: string | null,
      modelId?: string | null,
      reasoningEffort?: string | null,
      attachmentIds?: string[],
      webSearchConsented?: boolean,
    ) => {
      if (!chatId) return;
      run(
        `/api/v1/chats/${chatId}/messages`,
        {
          content,
          // Presence matters (chat/schemas.py): omit = append to active leaf;
          // explicit null = new ROOT sibling; uuid = sibling under that parent.
          ...(parentMessageId !== undefined ? { parent_message_id: parentMessageId } : {}),
          ...(modelId ? { model_id: modelId } : {}),
          ...(reasoningEffort && reasoningEffort !== 'off'
            ? { reasoning_effort: reasoningEffort }
            : {}),
          ...(attachmentIds && attachmentIds.length > 0 ? { attachment_ids: attachmentIds } : {}),
          // Fail-closed: only sent when the user explicitly consented on a
          // web-search-enabled workspace (see chat-page.tsx).
          ...(webSearchConsented ? { web_search_consented: true } : {}),
        },
        content,
      );
    },
    [chatId, run],
  );

  const regenerate = useCallback(
    (messageId: string) => run(`/api/v1/messages/${messageId}/regenerate`, {}, null),
    [run],
  );

  const abort = useCallback(() => abortRef.current?.abort(), []);
  const reset = useCallback(() => setState(IDLE), []);

  const stop = useCallback(() => {
    abortRef.current?.abort();
    setState(IDLE);
    // Server persists the partial answer on a detached task after disconnect;
    // refetch now and once more shortly after to catch the commit landing.
    void queryClient.invalidateQueries({ queryKey: ['chat', chatId] });
    window.setTimeout(() => {
      void queryClient.invalidateQueries({ queryKey: ['chat', chatId] });
    }, 750);
  }, [chatId, queryClient]);

  return { ...state, send, regenerate, abort, reset, stop };
}
