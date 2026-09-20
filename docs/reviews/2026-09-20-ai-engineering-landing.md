# Protected AI panorama landing review

> Historical review of commit 496077d4. The later user decision removes the separate UUID allowlist and reuses platform management roles; current behavior is documented in ../design/2026-09-20-ai-engineering-landing.md.

Review scope: uncommitted working tree relative to ca601c5e2b7486fa3d35f66a7bbcf6c906cc6eb1 in `/Users/neo/Developer/work/AI-Agent-Platform/.worktrees/ai-engineering-landing-20260920`, including the untracked backend content/router, client, page, tests, and design record. Requirements: `/Users/neo/Developer/work/Orbbec-AI-Engineering/docs/plans/2026-09-20-platform-panorama-landing.md`. Reviewed 2026-09-20; implementation was still changing during review.

## Final verdict

Ready to merge the reviewed panorama change. No remaining Critical, Important, or Minor findings from this review. All four original findings are resolved in the final working tree, with targeted regressions passing. This is a code-review/merge verdict, not production enablement or browser acceptance.

The broader frontend suite still has three pre-existing failures in `webui/src/legacyHrRoutes.test.ts`; do not report that full suite as green. This file is byte-identical to base ca601c5e, and this change does not modify the relevant HR parsing branches. Those obsolete retired-HR expectations are outside the panorama task.

## Resolved findings

- Important — Direct deep links could not reach login with missing/expired sessions. The exact `/brain` and `/ai-engineering` generic shell paths are now allowed through `is_public_request`, while all private APIs still require session authentication and exact allowlist membership. Root and preview-prefix HTTP tests pass.
- Important — Lazy image request failures retained the protected article. `AiEngineeringPage.tsx` now propagates the image `onError` through `clearFailedAsset`, clears index/document state, and displays the unavailable state. The regression dispatches an image error after a successful article render and verifies removal of the protected Markdown.
- Minor — Frontend login parsing accepted query aliases rejected by the backend. Both login return handling and page routing now compare exact raw `document=<known-slug>` strings; encoded values, trailing delimiters, duplicates, and additional parameters are rejected by the relevant tests.
- Minor — Ordinary employees at root lost the prior Brain access event. The root fallback now mounts the existing `AccessEventReporter` with the Brain descriptor alongside `BrainWorkspacePage`; it is mounted only when the access decision selects fallback. Panorama visits are not falsely counted as Brain.

Additional final changes reviewed: access probing now times out after five seconds, aborts pending work, and restores the ordinary homepage; late responses cannot reopen content. API requests explicitly use `cache: "no-store"`. Root panorama padding no longer applies viewport centering inside an already centered maximum-width article. No additional defect was found.

## Security and specification compliance

- Authorization uses trusted `AuthContext.internal_user_id` exclusively; no role, owner, name, department, or client header bypass. All four roles are covered by denial/allow/revocation tests.
- Every index, document, and asset request invokes the current file-backed allowlist. Empty configuration is closed; invalid/unreadable/non-private/symlink configurations close the probe and return a sanitized 503 for content. Account and Brain remain available.
- The existing private-file reader enforces absolute regular files, process ownership, restrictive permissions, bounded length, and no symlink following. The allowlist accepts only canonical UUID strings, limits cardinality, and rejects duplicate IDs and unsupported schema fields.
- Router templates are registered in central authorization, including their prefix-unwrapped forms. Authorization resolution occurs before content dispatch, and the router applies its own exact allowlist afterward. Prefix coverage passed.
- Content routes use fixed filename/slug maps, avoiding path composition from arbitrary input. Unknown and encoded traversal paths do not disclose files or fall through to a private HTML payload.
- All content namespace responses, including middleware-generated errors, receive private/no-store. Assets additionally use nosniff and restrictive SVG CSP. No conditional/304 content cache bypass was found.
- Account response contracts remain unchanged. Access capability is isolated to a boolean endpoint.
- Source pin and all eight files were independently compared byte-for-byte against `git show 69a9de2168de6fa42b14d586587169487797e6f3:<source_path>` in the engineering repository. All matched, including SVG and PNG, and all manifest digests matched.
- The private content package is outside webui/public and frontend imports. Docker copies it under backend app, while only Vite dist is installed into app/static. No representative private fact strings were found in webui/src or public. The final dist scan covered 66 files: all eight private whole-file byte sequences and digests were absent; raw/base64 SVG and PNG were absent; 232 substantive source Markdown lines were sampled at both ends (45-character fragments, both UTF-8 and JSON Unicode-escaped encodings), with no matches. No private Markdown, PNG, manifest, or panorama-named asset was present. The compiled panorama UI was present in the built JavaScript, as expected; fixed resource filenames/API routes are interface code, not private content.
- Markdown maps the six included document filenames and two image variants to protected same-origin API URLs with the active deployment prefix. Unsupported relative files become inert text, HTML is skipped, and remote images are suppressed. External HTTPS citations use noopener/noreferrer. The intentionally omitted organization/design/source-code evidence links are inert rather than public filesystem links.
- `/brain` is an explicit Brain entry; conversation creation/archive return and mission back-link now target it. `/agents` remains one click away. Home fallback and direct denial do not prefetch protected content.
- Account ID changes remount the session, abort requests, and prevent stale completion from restoring another account’s private content. Component tests include both pending access and already displayed snapshot cases. Document/index authorization failures drop rendered content, and image-load failures now also clear index/document state and remove the article.

## Verification and limits

Independently ran:

- `PYTHONPATH=backend /Users/neo/Developer/work/AI-Agent-Platform/backend/.venv/bin/python -m pytest -q backend/tests/test_ai_engineering_api.py`: 20 passed.
- Final selected frontend API, panorama page, auth, router, AppShell, Brain, access-event, and cloud-mode tests: 9 files, 232 tests passed. Only the existing jsdom scrollTo warning appeared.
- Exact pinned-source byte/digest comparison for eight private files: passed.
- Final public dist privacy scan: passed across 66 files, as detailed above.
- `git diff --check`: passed.

The parent reports 942 broader backend auth/config/HTTP/SPA/static checks passing; this reviewer did not rerun that entire set. The frontend implementation report records the final 11-file relevant suite at 270 passed, and `npm run build` (TypeScript plus Vite, 5,220 transformed modules) exiting 0. This reviewer independently ran the overlapping 9-file/232-test subset and inspected the resulting dist. An earlier full frontend run recorded 1,070 passed and six failures: three task-related expectations were fixed and covered by the final focused run; the other three are the unchanged retired-HR expectations. A clean full-suite result is not claimed. The full frontend suite is not wholly green because of the three unchanged retired-HR test expectations described above. Existing unsupported source-evidence links remain inert by design because only the six approved documents are published. No browser, production, messaging, commits, or source mutations were performed. Real DingTalk login, production allowlist configuration, mobile layout, and business acceptance remain outside this local review and require the planned user validation. The first real allowlist has not been supplied and production enablement is not claimed.

Final review boundary: browser/mobile acceptance was deliberately not performed under AGENTS.md. No deployment, real allowlist activation, or production acceptance was performed. The merge verdict does not waive those release gates.
