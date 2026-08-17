# Session Pagination Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every authorized Session reachable through stable, URL-backed pagination.

**Architecture:** Extend the existing Session filter URL state with a canonical page number and translate it to the backend's existing offset contract. Keep rendering and navigation in `SessionsPage`, with no backend or storage change.

**Tech Stack:** React 18, TypeScript, Fetch API, Vitest, jsdom.

## Global Constraints

- Page size is exactly 50.
- Page 1 is omitted from canonical URLs.
- Filters reset pagination to page 1.
- Backend authorization and Agent exclusions remain authoritative.
- Do not fetch the complete result set into the browser.

---

### Task 1: Canonical Session page state

**Files:**
- Modify: `webui/src/sessionNavigation.ts`
- Test: `webui/src/sessionNavigation.test.ts`

- [ ] Add failing tests for valid, invalid, and canonical `page` query values.
- [ ] Run `npm test -- src/sessionNavigation.test.ts` and verify RED.
- [ ] Add `page: number`, parse only positive base-10 integers, and omit page 1 from `sessionsPath`.
- [ ] Run the focused tests and verify GREEN.

### Task 2: Offset requests and page controls

**Files:**
- Modify: `webui/src/pages/SessionsPage.tsx`
- Modify: `webui/src/styles.css`
- Test: `webui/src/pages/SessionsPage.test.tsx`

- [ ] Add failing tests proving page 3 requests offset 100, controls expose the visible range, navigation updates the URL, filters reset page 1, and an out-of-range page is replaced with the last page.
- [ ] Run `npm test -- src/pages/SessionsPage.test.tsx` and verify RED.
- [ ] Implement fixed-size request offsets, canonical last-page correction, and first/previous/next/last controls.
- [ ] Add responsive pager styles consistent with the existing clean platform UI.
- [ ] Run the focused tests and verify GREEN.

### Task 3: Release verification

**Files:**
- No additional source files.

- [ ] Run `npm test`, `npm run build`, and `npm audit --omit=dev --audit-level=high` in `webui`.
- [ ] Run `git diff --check` and confirm the worktree is clean after commits.
- [ ] Push the verified commit to the feature branch and `master`, deploy with the protected environment file, and run DingTalk production acceptance.
- [ ] Confirm the public build contains the pager labels and both Platform and FAE health endpoints return 200.
