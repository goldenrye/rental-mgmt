export const PAGE_SIZE = 10;

export function paginateEntries<T>(entries: readonly T[], requestedPage = 1) {
  const pageCount = Math.max(1, Math.ceil(entries.length / PAGE_SIZE));
  const page = Math.min(pageCount, Math.max(1, Math.trunc(requestedPage) || 1));
  const start = (page - 1) * PAGE_SIZE;
  return {
    page,
    pageCount,
    entries: entries.slice(start, start + PAGE_SIZE),
    first: entries.length ? start + 1 : 0,
    last: Math.min(start + PAGE_SIZE, entries.length),
    total: entries.length,
  };
}
