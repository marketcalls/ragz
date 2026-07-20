type QueryErrorProps = { error: unknown; onRetry?: () => void };

/** Shown when a `useQuery`/`useInfiniteQuery` lands in `isError` — the
 * admin pages previously had no `isError` branch at all, so a failed query
 * silently rendered a blank content area (carried finding, Plan K Task 14).
 * Reuses the same `role="alert"` + `text-danger` visual language the
 * mutation-error paragraphs in invite-dialog.tsx/model-form-dialog.tsx use. */
export function QueryError({ error, onRetry }: QueryErrorProps): JSX.Element {
  const message = error instanceof Error ? error.message : 'Something went wrong.';
  return (
    <div className="flex flex-col items-start gap-2 rounded-md border border-line bg-raised p-4">
      <p role="alert" className="text-[12px] text-danger">
        Failed to load: {message}
      </p>
      {onRetry ? (
        <button
          type="button"
          onClick={onRetry}
          className="text-[12px] text-secondary underline underline-offset-2"
        >
          Retry
        </button>
      ) : null}
    </div>
  );
}
