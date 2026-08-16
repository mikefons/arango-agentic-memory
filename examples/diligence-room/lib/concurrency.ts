/**
 * Bounded, order-preserving concurrent map.
 *
 * Runs `fn` over `items` with at most `limit` calls in flight at once and returns the results in
 * **input order** (regardless of completion order). Used to parallelize independent per-item LLM
 * calls — e.g. per-document claim extraction — without an unbounded fan-out that would trip a
 * provider's rate limits. This is the app-level analog of the core's IN-7 extraction pool.
 */
export async function mapLimit<T, R>(
  items: readonly T[],
  limit: number,
  fn: (item: T, index: number) => Promise<R>,
): Promise<R[]> {
  const results = new Array<R>(items.length);
  let next = 0;
  const workers = Array.from(
    { length: Math.max(1, Math.min(limit, items.length)) },
    async () => {
      for (let i = next++; i < items.length; i = next++) {
        results[i] = await fn(items[i], i);
      }
    },
  );
  await Promise.all(workers);
  return results;
}
