# HR B whole-branch independent review

Reviewed 2026-09-10. Base `589f5d7`; supplied complete diff `.superpowers/sdd/B-final-review.diff` covers HEAD `7542ec0` plus the integration working tree. This review is read-only except for this report. Line references identify the reviewed snapshot; concurrent integration fixes may move them.

## Strengths

- B extends the existing authenticated runtime rather than introducing a second execution chain. The five-tool contract remains intact; standard confirmation is exposed only through explicit authenticated user HTTP. Anthropic gateway compatibility changes preserve server-side schema validation.
- Proposal preparation checks declared and inherited exact references, conservative candidate ancestry, the unique target position, and the actual standard base. Confirmation serializes the first standard slot as well as subsequent revisions, applies only selected changes, and revalidates source ancestry before replaying an idempotent receipt.
- Result downloads reuse the exact persisted revision and its transitive authorization checks. The export bytes do not depend on a model rerun or current revision alias. Service routing includes private/no-store response headers and the new platform authorization route entries.
- PDF/DOCX parsing uses an independent durable queue, attempt/lease fencing, bounded child execution, encrypted results, immutable source identity checks, and explicit partial coverage. The new 097 hash matches the readiness constant; the supplied branch diff leaves 096 unchanged.
- Retained knowledge releases preserve source bytes and pinned manifest identity. The runtime separates current discovery from recorded release validation; method choice remains autonomous.
- Tests cover real HTTP/DB identity and persistence boundaries separately from model doubles and frontend component mocks. The prior UI review fixes address async late responses, exact destructive-change display, and budget retry identity.

## Issues

### Critical

None found in the reviewed changes.

### Important — fix before declaring B complete

1. **P2: The result UI discards the authoritative basis classification.**
   - File: `webui/src/workspaces/hr/HrLoopWorkspace.tsx:1120` (`ResultCard`, saved-status/body rendering in the reviewed snapshot).
   - `SavedResult.basis` exists in the API type but is never read by the workspace. A valid calibration result with `basis=[{kind:"user_temporary",input_revision:1,ref:null}]` and an ordinary body displays only “成果已保存”; it has no “临时要求” classification. A `confirmed_standard` basis likewise has no structured identification.
   - This violates `docs/superpowers/specs/2026-09-09-hr-cloud-loop-runtime-spec.md:150` and `HR_Agent工作流.md:92`: saving and presentation must preserve the distinction between confirmed standards, official original material, and temporary user requirements. Model-written body prose is not a reliable substitute for the validated field.
   - Render concise basis labels directly from `result.basis`, preserving the relevant input revision/exact standard context without requiring the model to repeat it. Add component cases whose bodies deliberately omit these labels and verify the structured classification appears.

2. **P2: Revocation cleanup leaves private thread and position metadata on screen.**
   - File: `webui/src/workspaces/hr/HrLoopWorkspace.tsx:193` (`report` and `clearPrivate`; originally around lines 188–217 before concurrent edits).
   - On a work refresh/download/read returning 401/403/404/410, the component advances its epoch and clears messages/results/references, but retains `threads`, `history`, `position`, and the private draft `text`. Previously fetched thread titles remain rendered in the sidebar and the selected position name remains in the picker after the component has received explicit access denial. The backend thread projection itself rechecks these title sources, so retaining the old cached title bypasses that intended UI boundary.
   - Clear private sidebar/position/draft caches on the access-failure path as well. Keep ordinary work switching separate if its selection-preservation behavior is intentional. Extend the existing revocation/late-response component test with a private thread title and position title, assert they disappear after denial, and retain the stale-response assertion.

### Minor

1. **P3: Uploaded material references never receive the intended label.**
   - File: `webui/src/workspaces/hr/HrLoopWorkspace.tsx:871` (reference-chip label branch in the original snapshot; locate `r.kind === "material_text"`).
   - The real contract uses `ExactRef.kind="material"`, with original/text representation encoded in the ID. Therefore the `material_text` branch is unreachable and uploaded text is shown as the generic “已选参考”.
   - Match the real material kind/representation; optionally tighten the frontend kind union to the actual contract to catch this mismatch statically.

## Recommendations

- The standard panel could offer an explicit “带此标准讨论” action that adds its already-read exact `standard.ref` to the next input. The backend can already discover/read standards, so this is an optional usability improvement rather than a blocking finding.
- Finish the planned final documentation and browser acceptance updates, recording engineer/model/browser/user-review evidence separately. Existing broad backend/frontend suites need not be rerun solely for this review; targeted component regression is sufficient for these UI fixes, followed by the planned final browser pass.

## Evidence and limits

- Inspected the whole-branch changed-file inventory and the implementation paths for auth routes, service assembly, knowledge releases, resource/context authorization, result/proposal persistence, standard confirmation, parser worker/fencing/storage, model transport changes, frontend API/workspace, migration, and relevant contract/tests.
- Executed read-only `git diff --check` (passed), inspected the migration diff, and computed 097 SHA-256: `4ba8a48d988e2c88293c371cc4c5b46f89270a7c3918df8cd172858027d59cc4`, matching the readiness constant. No test suites, real model calls, candidate data, production or remote actions were initiated by this review.
- Root reports fresh backend regression in progress, auth 272 passed, frontend 118 plus auth 100 passed and build passed, and one public real-model journey passed. Those are reported upstream evidence, not commands independently rerun by this reviewer. The two P2 findings are established by the rendered component branches and state-cleanup paths, not a claimed browser reproduction.

## Assessment

**Ready for local B completion: With fixes.** Address the two P2 UI contract/privacy findings and verify them with focused component tests. No additional backend security/correctness blocker was found in the reviewed integration; browser acceptance and independent professional/user acceptance remain separate gates, with no implication of production readiness or authorization to enter C.

## Fix re-review — final assessment (2026-09-10)

Reviewed follow-up commit `11b347090da9b5b47d8982207a59e044aea70f25` over the integrated backend commit `c37eb1d`. This section supersedes the initial assessment above; the original findings remain as review history.

- **P2 basis presentation: resolved.** `ResultCard` renders the structured basis independently of model prose, labels confirmed standards and official material as the versions used by the saved result, and labels temporary requirements with the accepted input revision and explicit unconfirmed status. New parameterized tests cover all three classifications with bodies lacking basis labels. The frontend basis kind type is also narrowed to the actual contract.
- **P2 private caches after denial: resolved.** The access-failure branch now clears thread/history navigation, position/name, draft and other private interaction state in addition to the prior message/result clearing. Existing epoch invalidation remains before cleanup, preventing outstanding success responses from restoring the revoked data. New 403/410 tests first render private thread and position titles plus a draft, then deny work access and resolve a delayed result, and assert all private content is gone. Ordinary work switching preserves its separate intended behavior.
- **P3 material label: resolved.** Reference chips match `kind="material"` and distinguish `:text` from original representation. A new component test checks the real material reference shape.
- The optional exact-standard discussion action is present and submits the already-fetched `standard.ref`. The adjacent method-preview component changes presentation only: it omits YAML frontmatter, displays relative source links as text, and leaves the source text/reference passed into discussion unchanged. The new test verifies this reference preservation.

**Final code-review verdict: Yes for local B engineering handoff; no unresolved findings from this review.** The follow-up addresses both required fixes and the minor issue without relaxing backend authorization, exact-reference, or user-only confirmation boundaries. No additional blocker was found in this targeted re-review.

Verification remains explicitly separated:

- **Reviewer:** inspected the committed changes and regression assertions; `git diff --check` passed. No suites or browser/model calls were rerun.
- **Frontend engineer report:** seven observed failing cases followed by a passing combined 225-test regression (24 workspace tests), plus a passing production build. These are component/client tests, not real browser or model acceptance.
- **Root browser report:** real partial confirmation, work resume, method interaction, and a downloaded 125-byte file matching server file-info hash passed. The native file chooser opens, but automated file assignment is rejected with `NotAllowed`; completion of file selection through the native browser remains explicitly unverified. Real upload APIs have separate passing coverage and do not stand in for that browser interaction.
- **Acceptance boundary:** professional review of the public real-model output and B-stage user acceptance remain pending. Final documentation/report updates remain root-owned. This engineering verdict does not claim complete native-upload browser acceptance, business-quality approval, production acceptance, deployment authorization, or permission to enter C.

## Scroll-fix scope review (commit `40ba2c3`)

Inspected the seven-line CSS-only follow-up and the actual shell/loop DOM. `HrWorkspaceShell` renders its child directly inside `.hr-workspace-body`, and the loop supplies `<main className="hr-loop">`; therefore `.hr-workspace-body:has(> .hr-loop)` targets the intended container. Its higher specificity overrides the shared `overflow:hidden` rule with vertical scrolling while retaining horizontal clipping. The existing chat/position containers do not match this direct-child selector, so their scroll ownership is unchanged. The rule also covers the narrow flex-column layout that produced the browser-discovered clipping.

**Static verdict: accepted; no new finding.** The local engineering review verdict above remains unchanged. `git diff --check` passed. Root reports the build passed and is performing the actual scroll retest; this scope review did not run tests or independently verify wheel interaction. All previously documented native-file-selection and professional/user-acceptance limitations remain separate.
