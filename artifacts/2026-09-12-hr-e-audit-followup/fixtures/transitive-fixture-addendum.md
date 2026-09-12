# Transitive fixture follow-up

The final related integration found three consumers still applying the old pending result-recovery SQL after importing the deployed-schema direct-worker fixture. Independent RED runs reproduced `DuplicateColumn: progress_source_seq` before any business assertion:

- direct material transport: exit 1, 2 errors in 2.34s;
- knowledge HTTP: exit 1, 1 failed in 1.56s;
- reliable web loop: exit 1, 6 errors and 1 skipped in 6.64s.

All three duplicate migration calls were removed. Material transport then reached a real v6 authorization boundary: its fixture added attachment bindings after intake, while deployed 094 freezes attachment IDs during intake. That intermediate RED is retained separately: exit 1, 1 failed and 1 passed in 3.42s. The fixture now creates ready synthetic ledger rows first and submits their IDs through a real `ConversationTurnSubmission`, so binding and immutable HR scope are recorded atomically. Original SHA, frozen handoff, recovery, token-storage, grant count, and rollback assertions remain unchanged.

Individual GREEN results were material transport 2 passed in 3.42s, knowledge HTTP 1 passed in 2.32s, and reliable web loop 6 passed/1 skipped in 10.08s. Fresh combined verification was exit 0, 9 passed/1 skipped in 15.42s. The skip remains the pre-existing environment-gated real-process case.

The first broad Ruff run is retained in `transitive-ruff.log`; it reported established import-order and fixture-alias diagnostics plus introduced findings. Introduced findings were corrected. The final scoped lint command checks the new provenance file fully and checks `F401`, `F811`, and `RUF059` across the changed fixture consumers; both checks passed. Existing baseline style findings were not reformatted.
