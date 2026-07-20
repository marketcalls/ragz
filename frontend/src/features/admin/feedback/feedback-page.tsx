import { TopBar } from '@/components/layout/top-bar';
import { Button } from '@/components/ui/button';
import { QueryError } from '@/components/ui/query-error';
import { Spinner } from '@/components/ui/spinner';
import { Table, TBody, TD, TH, THead, TR } from '@/components/ui/table';

import { useFeedbackQueue } from './queries';

export function FeedbackPage() {
  const queue = useFeedbackQueue({ rating: 'down' });
  const items = queue.data?.pages.flatMap((p) => p.items) ?? [];

  return (
    <>
      <TopBar title="Feedback" />
      <div className="p-4">
        {queue.isPending ? <Spinner label="Loading feedback…" /> : null}
        {queue.isError ? (
          <QueryError error={queue.error} onRetry={() => queue.refetch()} />
        ) : null}
        {!queue.isPending && !queue.isError ? (
          items.length > 0 ? (
            <Table>
              <THead>
                <TR>
                  <TH>Question</TH>
                  <TH>Answer</TH>
                  <TH>Comment</TH>
                  <TH>When</TH>
                </TR>
              </THead>
              <TBody>
                {items.map((item) => (
                  <TR key={item.message_id}>
                    <TD>{item.question}</TD>
                    <TD>{item.answer}</TD>
                    <TD>{item.comment ?? '—'}</TD>
                    <TD>{new Date(item.created_at).toLocaleString()}</TD>
                  </TR>
                ))}
              </TBody>
            </Table>
          ) : (
            <p className="text-[13px] text-secondary">No thumbs-down feedback yet.</p>
          )
        ) : null}
        {queue.hasNextPage ? (
          <Button
            onClick={() => void queue.fetchNextPage()}
            disabled={queue.isFetchingNextPage}
          >
            Load more
          </Button>
        ) : null}
      </div>
    </>
  );
}
