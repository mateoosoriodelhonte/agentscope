/** What one page of a server-paged list says about the whole list. */
export interface PageInfo {
  /** Offset of the page's first row. */
  start: number
  hasPrevious: boolean
  hasNext: boolean
  /** "51–55 of 55"; without "of N" when the server did not report a total. */
  range: string
  /** Page 1 holds every result, so an empty filtered page means nothing matches at all. */
  complete: boolean
}

/**
 * `received` rows arrived for 1-based `page` of size `size`. With an unknown total (an older
 * server), a full page is the only sign that more may follow; no count is ever invented.
 */
export function pageInfo(page: number, size: number, received: number, total: number | null): PageInfo {
  const start = (page - 1) * size
  const hasNext = total !== null ? start + received < total : received === size
  const of = total !== null ? ` of ${total}` : ''
  const range = received ? `${start + 1}–${start + received}${of}` : `page ${page}${of ? ` (${total} in all)` : ''}`
  return { start, hasPrevious: page > 1, hasNext, range, complete: page === 1 && !hasNext }
}
