# D whole-branch engineering review

Reviewed baseline `95d33c3` through `464be4f`, the supplied diff package, both root HR contracts, D execution plan, prior task reviews/reports, and surrounding result, candidate, material, history, HTTP authorization, migration, UI, and validation-runner code. Concurrent final documentation/evidence and presentation-only UI work are outside the original commit-range verdict.

## Verdict

**APPROVED through the reviewed engineering delta `5d0d0af`.** The sole important correctness/authorization finding below is resolved by `e8f88ea`. The D runner extensions, general role clarifications, and presentation-only `08f811b` update introduce no additional substantive finding. This is an engineering review, not a real-model semantic-quality, human HR, browser, process-fault, or production acceptance.

## Final test/content delta — `5d0d0af`

**APPROVED.** Reviewed the `review_history` test-runner branch, committed run-3 provenance needed by that branch, and general role-content additions (including `176db8e`). This changes local validation and immutable knowledge-release source content, with no product runtime, authorization, or tool-count change.

The history branch verifies the fixture SHA against the current fictional scenario and compares each uploaded history file exactly against its saved result body. The two new material identities are recorded alongside the old result identities and body hashes; old refs are evidence metadata and are not injected as if they were valid result revisions in the new database. The prompt explicitly identifies the material as historical AI copies and the outputs as new-work results. Raw interview text is still separately registered through the existing user HTTP path, selected with JD/process materials, and checked unchanged after execution. The new branch cannot itself enable a real call: `HR_D_REVIEW_HISTORY` selects the scenario only inside the existing real-profile/evidence-gated test. The 24-call shared ceiling, default/extended per-call limits, and per-work budget remain unchanged.

Independently executed a read-only Python artifact check: the committed run-3 fixture digest matches `scenario.md`, both historical Markdown files exactly equal the saved bodies, and the selected kinds are exactly `candidate_assessment` and `interview_plan`. All seven run-3 artifacts used for this provenance are tracked. This was an artifact identity check, not a model call or semantic assessment. The coordinator supplied five focused scripted-flow/knowledge-release checks passing in 12.49 seconds; they were not rerun here.

Role additions preserve general distinctions between reported statements and verified facts, absent documentation and absent events, scoped requirements and global claims, and preferred validation methods and exclusive proof. They do not add fixture names, keyword routing, fixed workflow steps, automated ranking, or assertions that a particular output passed professional review. The separate run-4 semantic outcome remains outside this code-review verdict.

## Fix and runner re-review

`e8f88ea` repeats `_candidate` after the transcript byte read and before `_authorize_relations`, idempotency lookup, or writes. Both new registrations and replay receipts therefore pass the same current candidate authority check. The original interview-source lock and atomic record/marker/operation transaction remain intact.

I independently reran the local HTTP/PostgreSQL race probe against the fix and extended it to revoke the separate candidate source **during byte retrieval on an existing idempotent request**, in addition to the new registration case. Output:

```text
fresh_POST=410; new_record_marker_operation=(0, 0, 0); replay_race_POST=410
3 passed, 1 warning in 2.01s
```

This fresh command used the same in-memory pytest replacement mechanism described below, plus both parameterizations of `test_d_validation_port_limits_are_explicit`; it did not modify implementation or test files. The benign warning concerns an already-imported anyio module's pytest assertion rewriting. The checked-in regression additionally covers revocation before replay. The coordinator's separate 18-test pass is supplied evidence, not this reviewer's rerun.

The runner's extended 300-second / 16,384-output-token envelope requires `HR_D_EXTENDED_RESPONSE=1` inside the already opt-in real-model path. Default port limits remain 120 seconds / 4,096 output tokens. Model and protocol validation, the shared 24-call ceiling, and per-work 600,000-token / 900-active-second limits are retained. Extended output raises the local token reserve to 32,768 without rewriting the private profile or deployment configuration. Independent unit executions verified both default and extended limits reject excessive output/deadline and the 25th call. Stop/usage observations and elapsed duration do not include text/tool deltas or request credentials; runner revision and file hash make the local source version inspectable. The no-plan journey leaves `interview_plan_ref` null and verifies the exact raw record persists. Successful engineering assertions still require completed work and saved result kinds; the failed default run is not silently counted as a completed journey. No live model calls or real-model semantic review were performed during this re-review.

## Original P1 — resolved by `e8f88ea`

Interview registration could commit after candidate authority expired during attachment I/O.

Location: `backend/app/hr_agent/interview_records.py:88-115`, with the write at `130-145`.

`register` calls `_candidate` only before `materials.read_text`. After that potentially external byte read, `_authorize_relations` checks the optional relation and plan, and `_lock` checks only the interview attachment. Neither revalidates the candidate's separate confirmed source or its current position authority. When the optional plan and position are null, there is no candidate-authority check at all after the byte read. The existing exact-GET revalidation fix does not apply to POST or its replay receipt.

I reproduced this through authenticated local HTTP and a disposable real PostgreSQL database: create a candidate from attachment A, upload a separate interview attachment X, and monkeypatch only the `materials.read_text` boundary to expire A after X's real bytes have been read. Then POST a normal record registration without a plan/position. Observed output:

```text
POST status=201; persisted_records=1; subsequent_candidate_GET=410
1 failed, 1 warning in 1.89s
```

The failing assertion required POST 410 and zero newly persisted records. The successful registration instead creates a record, personal marker, and receipt for a candidate that has already become unavailable. This violates the current-authority/write and zero-persistence-on-rejection contract and can leave the user with a successful receipt for an immediately unreadable record.

Revalidate the candidate and relevant authority after attachment I/O and before any new registration write or replay response. Keep the interview-source lock and atomic record/marker/receipt transaction. Add a deterministic authenticated API regression covering expiry of a separate candidate source during interview byte retrieval, asserting rejection and no new record, personal marker, or operation; also exercise the replay response after the same race.

### Independent reproduction mechanism

Executed one Python-heredoc probe from `backend` using `.venv/bin/python`, calling `pytest.main` for `tests/test_hr_agent_interview_records.py::test_exact_read_rechecks_candidate_after_attachment_io`. A temporary in-memory `pytest_collection_modifyitems` plugin replaced that item's callable with the POST race probe while retaining its real `uploaded`, `intake`, `database`, and `monkeypatch` fixtures. Thus the displayed existing test name belongs to the harness; it was **not** a failure of the checked-in exact-GET test. No test or application file was changed. Candidate initialization uses the existing scripted model fixture; original uploads, attachment reads, HTTP identity/CSRF/idempotency and database writes are real local engineering paths. No network model, production, or browser call was made.

## Other reviewed boundaries

- Automatic result links are inserted in the save transaction from server-verified saved objects. Confirmation adds only a link to the exact accepted result identity; it does not rewrite historical result documents or hashes. Generic lists read current revisions, while candidate documents retain exact confirmation refs, including the historical fallback.
- Selected-result provenance traversal retains full exact identities, validates every result node against current work objects, checks owner and live leaf authority, and uses bounded deduplicated traversal. The history fix now preserves successfully read source text for the next model request, with an explicit payload assertion. Final history selection still revalidates dependencies and execution fence without reusing the per-read decision table.
- Fresh user interview uploads get the shared personal-material marker in the same transaction as the record and receipt. Existing downstream personal-processing and standard-proposal checks include both registration origins. Exact GET has the required post-byte-read candidate/relation check and interview-source lock.
- Migration 101 remains additive with owner-bound foreign keys, one registration-origin constraint, encrypted record metadata, limited grants, fixed SECURITY DEFINER search path, and revoked PUBLIC function execution. Independently computed SQL SHA256 is `9080e664256748eb5972c92478c5aea9b09a4a56ddd40395dff9266a81c582fa`, matching readiness configuration. No old migration edit or production migration was performed.
- Candidate UI resets selections/drafts on candidate changes, suppresses stale responses, separates confirmed exact drafts from associated current results, uses `source_ref`, and retains immutable upload stages plus idempotency key for retry. Full result object scope is checked before continuation. Parent-managed UUID presentation changes do not alter these backend contracts.
- The D runner remains opt-in for real calls, uses fictional inputs, pins the HR model/protocol, caps aggregate calls/output/deadline, and labels scripted candidate initialization separately. Role content distinguishes future plans, supplied raw records, and derived AI results without adding a sixth tool or model confirmation capability. Metering observations remain exploratory rather than calibration approval.

## Evidence limits

The coordinator supplied 420 passed / 6 skipped related backend checks, a supplemental no-plan journey pass, 18 post-fix interview/candidate-route checks, and 56 frontend checks plus TypeScript compilation. Those are **supplied evidence, not suites rerun by this reviewer**. Independent executions were the original failing POST race probe, the migration checksum check, and the three passing focused checks described in the re-review. No broad suite, process-fault test, browser acceptance, real model call, or production acceptance was performed during this review.
