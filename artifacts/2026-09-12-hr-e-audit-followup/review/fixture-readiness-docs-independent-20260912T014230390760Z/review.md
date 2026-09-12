# Independent fixture/readiness/provenance and status review

Scope: root requested independent review of the listed final source deltas against 192a448; none are this reviewer's authorship. Status docs were explicitly draft, awaiting the final 27-file integration. Read-only source/log review, no rerun, source/index mutation, production, model or browser work.

## Findings

No critical or important source-code finding. No assertion weakening identified in the repaired fixture/readiness/provenance coverage.

Pending final evidence/documentation: draft docs/reviews/2026-09-12-hr-e-audit-followup.md section 3 calls integration-1 final without stating its actual failure result. The preserved log and command JSON show exit 1, 7 failed, 486 passed, 1 skipped, 8 errors in 343.73s. Root already intends to update this after final integration. Retain that exact failed attempt and its causes separately, link the successful new attempt as final only after it completes. Section 4 W2/W7/W8/W9 should directly bind each row to the specific new logs/test evidence as requested by the plan; the current draft relies mostly on generic section 3 links. Both observations were sent immediately to root; final closure review pending.

## Source assessment

The shared deployed HR fixture is already present at baseline. Attachment and direct/summary imports now use its complete real hr_web migration chain; direct Worker no longer assembles/drops partial pending schemas. Progress, knowledge HTTP, material transport and web reliable-loop remove duplicate recovery SQL already applied by this fixture. This addresses DuplicateColumn errors without removing business assertions or changing product SQL.

The direct summary test explicitly becomes non-HR using a synthetic capability card, preserving summary-phase assertions while avoiding the intentional HR D1 current-turn isolation contract. This is a disclosed change of subject, not a claim that HR summary continuity passes. Persisted NULL HR context coverage separately checks history, assistant content and mixed summary suppression with both builder entrypoints. Its transient mutant fails narrowed history/summary protection; the docs correctly refrain from claiming the short fixture independently mutation-tests compaction threshold behavior.

Material transport now creates synthetic ready archive rows first and submits attachment_ids plus active_attachment_ids through the application repository, so actual deployed record_turn_scope_v6 records the input scope. Former direct insertion of turn binding is removed. Original attachment identity/order/hash, handoff grant scopes, frozen recovery equality, ciphertext token non-disclosure, persisted token hashes, and grant-failure rollback assertions remain. Synthetic existing archives are explicitly not upload/readiness acceptance.

Signed readiness tests clear fixture observations and post through the real signed endpoint. Positive samples advertise the v6 capabilities that persisted scoped HR turns require; the new negative submits an accepted/stored v5-only observation, confirms a non-NULL scoped turn, and asserts no claim plus unchanged queued identity with executor_capability_missing. Existing claim/replay/concurrency assertions remain; this is a corrected contract sample with a regression guard, not weakened admission.

The original direct provenance test was renamed as deployed behavior rather than falsely retaining upgrade coverage. The separate new provenance-upgrade test restores the temporal contract: exact 089 and 090, historical rows with both turn provenance columns verified absent, changed conversation route, actual 091 as platform_control_owner, then legacy_api_v1/0 backfill, original mission claimable, Worker adoption rejected. Non-HR intake followed by persisted HR identity is honestly labeled historical fixture setup; it does not claim current HR intake works before 094. Dropping origin_route_epoch CASCADE in a separate disposable fixture deliberately preserves the missing-schema failure case; it is not a success-path bypass.

## Status and evidence assessment

Recorded first integration failures correspond to six readiness cases with obsolete v5 samples, one duplicate-migration knowledge HTTP failure, and eight duplicate-migration material/web-loop setup errors. The reviewed changes target those causes, but this reviewer has not rerun them or inferred green results.

Root architecture/workflow updates preserve explicit failure-draft decisions, owner-preserving drain, optional approved old executor restore and production prerequisites. Previous review now preserves old numbers as their separate historical runs, discloses absent original 25-pass and eight intermediate frontend logs, keeps three styles failures unresolved, and narrows PM2 capability evidence to local checked-in scripts with production identity unverified. No professional W4/W11 or production quality status is upgraded by test counts. New draft describes 30s timeout as setting-only evidence and failed-draft tests as application-role repository/DB rather than HTTP, correctly.

## Disposition

Reviewed source deltas are acceptable for local engineering integration. Final pass claim remains conditional on root's fresh integrated evidence and final document update. No production deployment approval or professional/business acceptance follows from this review. fingerprints.json records the reviewed draft snapshot; later closure evidence must be separate.
