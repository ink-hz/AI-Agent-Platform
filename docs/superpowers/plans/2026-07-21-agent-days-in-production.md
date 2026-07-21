# Agent Days in Production Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Agent age immediately scannable by showing elapsed days in production as the primary Overview value while retaining the first-live date as supporting evidence.

**Architecture:** Add one pure lifecycle-age formatter beside the existing lifecycle formatters, then compose the Overview metadata row from the existing `live_since` and `last_updated_at` fields. No API or stored-data changes are required; age remains a client-side projection of durable lifecycle metadata.

**Tech Stack:** React 19, TypeScript, Vitest, CSS, Vite.

## Global Constraints

- Use `In Production`, never `Running Days` or `Uptime`, on Overview cards.
- Render elapsed full 24-hour days; values below one day display `Today`.
- Render `1 day` for exactly one full day and plural `days` thereafter.
- Keep `Since <date>` and the Last Updated calendar date as supporting evidence.
- Missing or invalid timestamps display `Not recorded` without fabricated secondary text.
- Preserve all unrelated user changes and restart only AI Agent Platform.

---

### Task 1: Lifecycle age formatter and card hierarchy

**Files:**
- Modify: `webui/src/fleet.test.ts`
- Modify: `webui/src/fleet.ts`
- Modify: `webui/src/copy.test.ts`
- Modify: `webui/src/copy.ts`
- Modify: `webui/src/FleetAgentCard.test.tsx`
- Modify: `webui/src/FleetAgentCard.tsx`
- Modify: `webui/src/styles.css`

**Interfaces:**
- Consumes: `FleetAgent.live_since`, `FleetAgent.last_updated_at`, and the card `now: Date` prop.
- Produces: `formatDaysInProduction(value: string | null, now?: Date): string` and the `In Production` card block.

- [ ] **Step 1: Write failing formatter tests**

Add these assertions to `webui/src/fleet.test.ts`:

```ts
expect(formatDaysInProduction("2026-07-21T01:00:00Z", new Date("2026-07-21T02:00:00Z"))).toBe("Today");
expect(formatDaysInProduction("2026-07-20T02:00:00Z", new Date("2026-07-21T02:00:00Z"))).toBe("1 day");
expect(formatDaysInProduction("2026-06-17T02:00:00Z", new Date("2026-07-21T02:00:00Z"))).toBe("34 days");
expect(formatDaysInProduction(null, new Date("2026-07-21T02:00:00Z"))).toBe("Not recorded");
expect(formatDaysInProduction("invalid", new Date("2026-07-21T02:00:00Z"))).toBe("Not recorded");
```

- [ ] **Step 2: Write failing card and copy tests**

Update `FleetAgentCard.test.tsx` to require `In Production`, elapsed days, `Since Jun 17, 2026`, `Last Updated`, and the supporting update date. Require that the Overview card no longer contains the `Live Since` label. Update `copy.test.ts` to require the `In Production` field label.

- [ ] **Step 3: Run focused tests and verify RED**

Run: `npm test -- --run src/fleet.test.ts src/FleetAgentCard.test.tsx src/copy.test.ts`

Expected: FAIL because `formatDaysInProduction` and the new card hierarchy do not exist.

- [ ] **Step 4: Implement the pure age formatter**

Add to `webui/src/fleet.ts`:

```ts
export function formatDaysInProduction(value: string | null, now = new Date()): string {
  if (value === null) return "Not recorded";
  const timestamp = new Date(value).getTime();
  if (Number.isNaN(timestamp)) return "Not recorded";
  const days = Math.floor(Math.max(0, now.getTime() - timestamp) / 86_400_000);
  if (days === 0) return "Today";
  return `${days} day${days === 1 ? "" : "s"}`;
}
```

- [ ] **Step 5: Implement the card hierarchy and styles**

Change the third copy field to `In Production`. Render the age in a strong primary element, render `Since ${formatLifecycleDate(...)}` only when the date is valid, and render the Last Updated calendar date under its relative value. Style primary lifecycle values at or above the existing card metadata size and supporting values at the approved minimum `11.5px`.

- [ ] **Step 6: Run focused tests and verify GREEN**

Run: `npm test -- --run src/fleet.test.ts src/FleetAgentCard.test.tsx src/copy.test.ts`

Expected: all selected tests pass.

- [ ] **Step 7: Commit the card feature**

```bash
git add webui/src/fleet.test.ts webui/src/fleet.ts webui/src/copy.test.ts webui/src/copy.ts webui/src/FleetAgentCard.test.tsx webui/src/FleetAgentCard.tsx webui/src/styles.css
git commit -m "feat: emphasize Agent days in production"
```

### Task 2: Full verification and Platform deployment

**Files:**
- Verify: `webui/`
- Deploy: existing `com.orbbec.ai-agent-platform` LaunchAgent

**Interfaces:**
- Consumes: production build from Task 1.
- Produces: the locally deployed Overview card hierarchy.

- [ ] **Step 1: Run the complete frontend suite**

Run: `npm test -- --run`

Expected: all tests pass with no warnings or failures.

- [ ] **Step 2: Build production assets**

Run: `npm run build`

Expected: TypeScript and Vite exit successfully.

- [ ] **Step 3: Restart only Platform**

Run: `launchctl kickstart -k gui/$(id -u)/com.orbbec.ai-agent-platform`

Expected: the Platform LaunchAgent receives a new PID; MetaBot, AI FAE, and AI ADMIN remain untouched.

- [ ] **Step 4: Verify deployed assets and health**

Confirm `/api/health` returns `{"status":"ok"}` and the served production JavaScript contains `In Production`, `Since`, and `Last Updated` without the rejected `Live Since` Overview label.
