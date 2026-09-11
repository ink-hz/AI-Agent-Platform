# Task 3 D3 frontend report

## Delivered

- Added a separate `候选人工作` entry while retaining the existing `批量简历材料` intake.
- Added candidate-scoped loading for confirmed candidates, exact confirmed drafts, associated results, original-material downloads, extracted source references, and interview records.
- Added explicit `用户提供` and `AI 整理` labels and an editable continuation intent. The UI does not introduce workflow routing buttons.
- Added raw UTF-8 interview transcript upload through the ordinary attachment lifecycle, then registration through the interview-record API. Retries retain upload/content/completion stages and the registration idempotency key; edited input creates a new immutable attempt.
- Candidate changes clear draft, selections, previews, pending upload state, and busy state. Epoch checks ignore late reads and save responses.
- Workspace continuation starts from `selectWork()`, then applies only the selected candidate, optional explicitly related position, exact references, and editable intent.
- Added local rejection of ambiguous results queries and incompatible candidate/result position scope.

## Verification

TDD red evidence was observed before implementation:

- API tests failed because candidate interview methods and candidate result query support did not exist.
- Component test failed because `HrLoopCandidateWorkspace` did not exist.
- Workspace integration test failed because the new entry was absent.

Fresh final commands:

```text
cd webui
npm test -- --run src/workspaces/hr/HrLoopWorkspace.test.tsx src/workspaces/hr/HrLoopCandidateWorkspace.test.tsx src/workspaces/hr/HrLoopCandidatesPanel.test.tsx src/hrLoopCandidatesApi.test.ts src/hrLoopApi.test.ts
```

Result: 5 files passed, 56 tests passed, 0 failed.

```text
cd webui
npx tsc -b
```

Result: exit 0, no TypeScript diagnostics.

## Review fixes

Independent review identified and the implementation fixed: stuck busy state after candidate switch, trimmed-title retry duplication, missing original/source access, missing AI authorship label, incomplete exact-result scope checks, upload polling/stage retention, Unicode code-point limits, an invented `documents[].text_ref` field, and unprefixed ticket URLs.

## Limits

- No browser acceptance was run, per task handoff; root owns later browser verification.
- No production, backend, database, or model calls were made.
- The current UI intentionally omits an interview date field and sends `occurred_at: null`, which is an accepted interface option.
- Related positions use a generic authorized label in the candidate panel; the main workspace resolves a known title through the existing position API.
