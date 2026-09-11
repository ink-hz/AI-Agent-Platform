# HR handoff and drain review evidence — 2026-09-11

Baseline: `b974a87`, branch `feat/hr-cloud-loop-e-review`. Local disposable PostgreSQL only; no production writes, model calls, credentials or original E artifacts changed.

## Verified changes

- `/v5/handoff` reacquires the shared cutover lock before each candidate's domain locks and offer. Persisted, authorized legacy work may continue in `legacy`/`draining_legacy`; `cloud`/`draining_cloud` returns 204 without offering it. Acceptance and source-event callbacks retain their existing independent identity/lease/provenance checks. No other Bot receives a lane rejection.
- `append_turn` and `resume_search_turn` take the shared gate before the conversation lock, then classify HR from the locked conversation and a fresh binding query. Other Bot turns participate in lock ordering but do not receive HR lane checks.
- Legacy ready-draft confirm/dismiss transactions hold the legacy continuation gate. They can finish during `draining_legacy` and cannot mutate after cloud activation. Their mutation endpoints, including replays, remain blocked cross-lane; this does not adopt or migrate ready drafts. Retry remains new admission.
- Additive migration **103** replaces only `hr_execution_cutover_counts_v102()`; 102 bytes, function API, owner and grants remain unchanged. Queued v5 envelopes are excluded only with exact job/binding/transport-run/Attempt/Turn provenance and terminal Attempt+Turn. Unknown/orphan lineage blocks. Pending unacknowledged stops and live linked Turn/Attempt block regardless of Relay status. Historical interrupted records with recorded terminal time, no pending stop and no live linked work are terminal for drain accounting only. No job is rewritten as completed or removed.
- The binding → signed transport fixture chain now applies the deployed HR schema once instead of pending-only fragments. The projection fixture no longer reapplies the already installed pending recovery SQL.

## Evidence and reproduction

Run from the worktree root with:

```sh
PATH=/opt/homebrew/opt/postgresql@17/bin:$PATH backend/.venv/bin/python -m pytest <files> -q
```

| Evidence | Files / result |
| --- | --- |
| `dispatch-red-2.log` | Initial gate regressions: **6 failed, 5 passed**. Wrong-lane signed handoff returned 200; handoff did not wait for cutover lock; both ready-draft mutations wrote after real cloud transition; concurrent binding escaped append classification. |
| `counts-red-3.log` | Initial count regressions: **5 failed, 4 passed**. Three completed v5 envelopes still counted active; interrupted pending stops and an HR job linked to live work were missed. |
| `deployed-transport-regression.log` | `test_hr_direct_command_binding.py test_execution_transport_v5.py`: **59 passed** after the shared fixture repair. |
| `final-gate-projection-regression.log` | `test_hr_cutover_count_review.py test_hr_cutover_dispatch_review.py test_turn_result_projection.py test_hr_execution_cutover.py test_hr_candidate_repository.py`: **54 passed**. |
| `final-counts-permissions.log` | Final count suite including one added permissions case: **11 passed**. App-role control execution denied; maintenance-role count and transition succeed. |
| `production-fixture-regression.log` | Earlier integration run including `test_agent_brain_conversation_repository.py`: **68 passed**. Includes overlapping gate/transport cases; do not sum these counts. |
| `legacy-regression.log`, `fixture-diagnostic.log`, `fixture-baseline-confirmation.log` | Preserved diagnosis of the old pending-only fixture's missing `record_turn_scope_v6` function. Exact baseline `append_turn` reproduced the same setup error in an isolated Python process; the final deployed fixture run above replaces that failed acceptance attempt. |

Initial fixture-construction errors and intermediate green logs are retained, clearly separate from the clean RED and final results above.

## Scope and limits

The new dispatch cases use actual loopback socket HTTP, generated Ed25519 worker signatures, the production verifier, persisted nonces, encrypted command binding, leases and real PostgreSQL transactions. Concurrency is observed through PostgreSQL lock waits in both handoff/cutover orderings and in ready-draft/cutover ordering. Candidate mutations use the real repository and security-definer database functions; user-facing candidate HTTP authentication is not separately re-tested here.

The three v5 completion cases go through signed handoff, signed acceptance, signed result source and the fenced projector; successful Attempt/Turn terminal state is not forged with SQL updates. The worker result and stop-proof content are synthetic contract fixtures: no native executor or model is launched, and this is not a real-process crash/stop or model-quality test. Historical/interrupted records and malformed residual states are explicit disposable fixtures. Wrong-lane dispatch cases inject an opposite-lane residual state; the real transition correctly rejects live work. Browser and production acceptance were not performed.
