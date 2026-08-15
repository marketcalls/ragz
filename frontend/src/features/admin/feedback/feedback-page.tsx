import { useState } from 'react';

import { TopBar } from '@/components/layout/top-bar';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { QueryError } from '@/components/ui/query-error';
import { NativeSelect } from '@/components/ui/select';
import { Spinner } from '@/components/ui/spinner';
import { Table, TBody, TD, TH, THead, TR } from '@/components/ui/table';

import { useUsers } from '../users/queries';
import { type FeedbackFilters, useFeedbackQueue } from './queries';

export function FeedbackPage() {
  const [rating, setRating] = useState('');
  const [userId, setUserId] = useState('');
  const [startDate, setStartDate] = useState('');
  const [endDate, setEndDate] = useState('');

  const users = useUsers();

  // Half-open [start, end) is enforced by the backend; widen the bare date
  // inputs to full-day bounds so the whole end day is included (< end).
  const filters: FeedbackFilters = {
    rating: rating || undefined,
    user_id: userId || undefined,
    start: startDate ? `${startDate}T00:00:00` : undefined,
    end: endDate ? `${endDate}T23:59:59.999` : undefined,
  };

  const queue = useFeedbackQueue(filters);
  const items = queue.data?.pages.flatMap((p) => p.items) ?? [];

  return (
    <>
      <TopBar title="Feedback" />
      <div className="p-4">
        <div className="mb-4 flex flex-wrap items-end gap-3">
          <div>
            <Label htmlFor="fb-rating">Rating</Label>
            <NativeSelect
              id="fb-rating"
              className="w-36"
              value={rating}
              onChange={(e) => setRating(e.target.value)}
            >
              <option value="">All</option>
              <option value="up">Positive</option>
              <option value="down">Negative</option>
            </NativeSelect>
          </div>
          <div>
            <Label htmlFor="fb-user">User</Label>
            <NativeSelect
              id="fb-user"
              className="w-56"
              value={userId}
              onChange={(e) => setUserId(e.target.value)}
            >
              <option value="">All users</option>
              {(users.data ?? []).map((u) => (
                <option key={u.id} value={u.id}>
                  {u.email}
                </option>
              ))}
            </NativeSelect>
          </div>
          <div>
            <Label htmlFor="fb-start">From</Label>
            <Input
              id="fb-start"
              type="date"
              className="w-40"
              value={startDate}
              onChange={(e) => setStartDate(e.target.value)}
            />
          </div>
          <div>
            <Label htmlFor="fb-end">To</Label>
            <Input
              id="fb-end"
              type="date"
              className="w-40"
              value={endDate}
              onChange={(e) => setEndDate(e.target.value)}
            />
          </div>
        </div>
        {queue.isPending ? <Spinner label="Loading feedback…" /> : null}
        {queue.isError ? (
          <QueryError error={queue.error} onRetry={() => queue.refetch()} />
        ) : null}
        {!queue.isPending && !queue.isError ? (
          items.length > 0 ? (
            <Table>
              <THead>
                <TR>
                  <TH>Rating</TH>
                  <TH>User</TH>
                  <TH>Question</TH>
                  <TH>Answer</TH>
                  <TH>Comment</TH>
                  <TH>When</TH>
                </TR>
              </THead>
              <TBody>
                {items.map((item) => (
                  <TR key={item.message_id}>
                    <TD>{item.rating === 'up' ? '👍' : '👎'}</TD>
                    <TD>{item.user_email ?? '—'}</TD>
                    <TD>{item.question}</TD>
                    <TD>{item.answer}</TD>
                    <TD>{item.comment ?? '—'}</TD>
                    <TD>{new Date(item.created_at).toLocaleString()}</TD>
                  </TR>
                ))}
              </TBody>
            </Table>
          ) : (
            <p className="text-[13px] text-secondary">No feedback matches these filters.</p>
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
