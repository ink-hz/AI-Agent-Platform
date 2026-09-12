# Provenance upgrade coverage addendum

Independent review correctly found that the repaired deployed-schema fixture no longer exercised the original temporal migration contract. `backend/tests/test_hr_direct_provenance_upgrade.py` restores it in a separate disposable database without changing the frozen shared fixture files.

The database receives the real base migration chain through 088, followed by the exact `hr_web/089_hr_turn_attempts.sql` and `090_hr_v5_readiness.sql`. A historical direct turn is then persisted. Because current HR intake requires the later 094 `record_turn_scope_v6` function, the fixture creates the row through the historical non-HR direct path and changes only the persisted conversation and mission agent identity to `hr-bot`; this is historical data construction, not evidence that current intake executed before 094.

Before applying 091, the test queries `pg_attribute` and requires both turn provenance columns to be genuinely absent. It changes the conversation route to `worker_direct`, epoch 7, applies the exact `091_hr_direct_dispatch.sql` as `platform_control_owner`, and verifies:

- the existing turn is backfilled as `legacy_api_v1`, epoch 0;
- its existing mission remains claimable by the legacy mission repository;
- `TurnAttemptRepository.create_queued(..., "worker_direct")` rejects adoption with `AttemptNotFound`.

No constraints are disabled and no migration order is fabricated. `provenance-upgrade-first-run.log` is the first execution: exit 0, 1 passed in 1.17s. `provenance-upgrade-final.log` is the fresh completion verification: exit 0, 1 passed in 1.13s. Exact commands are recorded in `commands.tsv`.
