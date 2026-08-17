# Session Pagination Design

## Context

The Session API already returns `items`, `total`, `limit`, and `offset`, and
accepts offsets up to the authorized result size. The Session page always asks
for `limit=50` without an offset and renders no navigation, so users can only
reach the first 50 authorized Sessions even though the remaining records are
present in the cloud replica.

## Design

- Use a fixed page size of 50 and offset pagination supported by the existing
  API. No backend or replica format change is required.
- Add a positive integer `page` to Session URL state. Page 1 is canonical and
  omitted from the URL; later pages use `page=2`, `page=3`, and so on.
- Request `offset=(page-1)*50` and show the server-reported total.
- Render the visible range, current/total pages, and controls for first,
  previous, next, and last page. Disable controls that have no valid target.
- Page navigation creates browser history. Back and forward restore both the
  filters and page.
- Applying search, Agent, or source filters resets to page 1.
- Invalid URL page values resolve to page 1. If data changes and the requested
  page is beyond the last page, replace the URL with the last valid page.
- Continue to rely on the backend for authorization and excluded-Agent
  filtering. Pagination reveals only records already authorized for the user.

## User Copy

The footer shows `第 X–Y 条，共 N 条` and `第 P / T 页`. Controls are labelled
`首页`, `上一页`, `下一页`, and `末页`.

## Verification

- `page=3` requests `limit=50&offset=100` while preserving all filters.
- Changing any filter requests page 1 and removes `page` from the URL.
- Page controls update URL state and browser history correctly.
- Invalid and out-of-range pages are canonicalized without exposing an empty
  false result.
- Existing Session search, history restoration, authorization, and detail
  navigation tests continue to pass.

## Non-goals

- Fetching every Session into the browser at once.
- Cursor pagination or changes to Session ordering.
- A user-selectable page size or direct arbitrary page input.
