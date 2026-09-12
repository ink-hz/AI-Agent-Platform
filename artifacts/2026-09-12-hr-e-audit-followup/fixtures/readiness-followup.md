# Readiness transitive fixture follow-up

Only test_execution_readiness_v5.py changed; no production behavior, shared fixture or auth implementation edits.

Cause: the deployed-schema worker_turn has persisted v6 HR scope. This test file still published signed v5-only readiness. The observation was correctly accepted as an authentic v5 observation, but could not authorize a v6 turn. Six positive lease tests failed. Negative tests could previously pass for the wrong (protocol-incompatibility) reason.

Fix: a local observation builder now promotes the existing well-formed sample to v6 with the real role-package/tool capability contract. All calls still pass through the signed readiness endpoint and actual validator/admission transactions. Existing signature/body, initial queue, held attempt, exact lease, delayed admission, expiry, cancellation and concurrent claiming assertions remain. Added explicit valid/signed/stored v5 observation rejection for the actual persisted scoped turn, including unchanged queued/no-executor/no-run state. Positive lease now also asserts v6 admission identity.

Evidence (all unique logs contain commands and exits):

- readiness-20260912T013742517066Z-baseline.log: 6 failed,14 passed,exit1.
- readiness-20260912T013838059527Z-green.log:21 passed,exit0.
- readiness-20260912T013856078421Z-lint.log:ruff2 import-order errors,exit1; diff-check0.
- readiness-20260912T013908891470Z-lint-fixed.log:import-only auto-fix0,ruff0,diff-check0.

The pytest run preceded the final import-only lint fix; root integration will run the frozen final source. No production/model/browser acceptance claim. File frozen after lint fix.

Final SHA-256: b70c68bc9ad3225bb661bc8d7fd6a7a5fd9e4b0da0fcbb329857df0fb01fe3eb
