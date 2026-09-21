import type { QueryClient } from '@tanstack/react-query';

/** Remove every identity-bound query before an auth boundary is crossed.
 *
 * `cancelQueries` reverts observers and signals query functions that honor
 * AbortSignal. `removeQueries` then destroys those query objects, so a request
 * that ignores cancellation and resolves later cannot reinsert its old result
 * into a new principal's cache.
 */
export async function clearAuthenticatedQueryState(queryClient: QueryClient): Promise<void> {
  await queryClient.cancelQueries();
  queryClient.removeQueries();
}
