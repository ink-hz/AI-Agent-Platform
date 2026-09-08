# Task 3 — Explicit HR intelligence reference handoff

## Scope and contract

- Added the shared `HrIntelligenceReference` contract and one formatter at `webui/src/workspaces/hr/hrIntelligenceReference.ts`.
- The existing submission contracts remain unchanged:
  - new conversation: `startConversation(input, csrfToken, "hr-bot"[, scope])`;
  - existing conversation: `createMessageSubmission(conversationId, input, csrfToken)`;
  - `input` remains a string without attachment capability and remains `TurnSubmission` with the formatted value in `text` when attachment state is present.
- Selected references are bounded user-selected data serialized into the durable user text. They are not server-authoritative metadata and add no backend/runtime field.
- This handoff does not implement ordinary autonomous topic discovery or resolve M1/M2/M3. No model, worker, production, or business-message call was made.

## RED

1. `npm test -- --run src/workspaces/hr/hrIntelligenceReference.test.ts`
   - Failed because `./hrIntelligenceReference` did not exist.
2. `npm test -- --run src/workspaces/hr/HrWorkspacePage.test.tsx`
   - The three initial integration cases failed because selection was neither stored/displayed nor routed, and the retry path had no serialized reference payload.

## GREEN

- Formatter tests enforce pinned identity, label, excerpt, source data, explicit non-instruction wording, independent user text, and a hard aggregate 12 KiB UTF-8 reference budget. Oversized selections throw; no content is silently truncated.
- `HrWorkspacePage` owns references in an `internal_user_id` keyed map. Explicit selection deduplicates by key, never sends, preserves the mounted draft and attachments, and returns to the last known chat or `/hr/`.
- Both new and existing composers render inspectable/removable selections and a company return link. Remove controls follow pending/read-only policy.
- The frozen submission key contains the actual serialized text and attachment state. Failed retry reuses the exact submission object. Successful submission removes only keys captured by that submission; a choice added while it is pending remains selected.
- Actual 32 KiB conversation sizing uses the combined serialized text. Reference-only submission is possible only after the user explicitly presses send; selection never autosends.

Fresh verification:

```text
npm test -- --run src/workspaces/hr/hrIntelligenceReference.test.ts src/workspaces/hr/HrWorkspacePage.test.tsx src/pages/ConversationPage.test.tsx src/pages/AgentUsePage.test.tsx src/workspaces/hr/HrPositionWorkspace.test.tsx
5 files passed; 111 tests passed.

npm run build
TypeScript build and Vite production build exited 0.
```

The jsdom suite prints its existing `scrollTo` and localStorage environment warnings; it has no test failures. Vite prints its existing large-chunk advisory.

## Self-review

- Confirmed user text stays in editor state and reference state changes do not mutate attachment arrays.
- Confirmed account changes expose only that account's selections.
- Confirmed formatter output contains bundle/company/unit/local identities where supplied and encodes source URLs as data under an explicit non-instruction preamble.
- Confirmed no Task 2 reading component, API/parser, backend, migration, runtime, or production path was edited for this task.
- Browser acceptance and production acceptance were not performed here. The parent integration task owns real HTTP persistence/readback validation; this task's evidence is frontend component integration and production build only.

## Independent review follow-up

Two reviewer findings were reproduced with focused failing tests:

1. Switching from account A's conversation to account B while the panorama was open allowed B's explicit selection to reuse A's `lastChatTarget`.
2. Scalar identity fields other than `reference_key` could inject line delimiters into the serialized reference record.

The host now keeps last chat targets in an `internal_user_id` keyed map, matching reference and draft ownership. The formatter now applies `JSON.stringify` to every scalar string, including bundle, company, generation time, unit, claim type, and local identity. The aggregate 12 KiB budget is still measured from the final encoded reference material.

Focused RED command:

```text
npm test -- --run src/workspaces/hr/hrIntelligenceReference.test.ts src/workspaces/hr/HrWorkspacePage.test.tsx
2 expected failures: raw delimiter injection and cross-account retained-chat routing.
```

Focused GREEN command:

```text
npm test -- --run src/workspaces/hr/hrIntelligenceReference.test.ts src/workspaces/hr/HrWorkspacePage.test.tsx
2 files passed; 27 tests passed.
```

## Company reading continuity follow-up

The host previously unmounted `HrPanoramaWorkspace` as soon as selection navigated back to chat. That discarded the child workspace's search, evidence expansion, job filters/page, pinned bundle, and reading position.

`HrWorkspacePage` now retains the visited panorama child for the current `internal_user_id`, hides it from layout and the accessibility tree while another HR surface is active, and reuses the same keyed child on return. An account change unmounts the prior child and starts a fresh account-scoped host, so no company content or pinned reading bundle crosses accounts. Fresh application entry still mounts a new workspace and therefore reads the latest publication.

Visible panorama scroll is captured by scoped `scroll` and `platform:navigate` listeners. Return restoration uses two animation frames so it runs after the router's scheduled `scrollTo(0, 0)`; effect cleanup cancels either pending frame. The chat host and its own draft/scroll behavior are unchanged, and retention never invokes submission.

Focused RED:

```text
npm test -- --run src/workspaces/hr/HrWorkspacePage.test.tsx
3 expected failures: panorama child unmounted, account change reused child state, and no restoration frame was scheduled.
```

Focused GREEN:

```text
npm test -- --run src/workspaces/hr/HrWorkspacePage.test.tsx
1 file passed; 26 tests passed.
```
