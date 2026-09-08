# HR WEB opt-in production migrations

The production inventory on 2026-09-08 was through 087. Apply the normal root
migrations through 088, then this directory through 094 using the existing
control-plane migrator and the same checksum ledger. Numbers 089–094 are reserved
here and must not be allocated again in the root directory.

These are the unchanged SQL bodies exercised by the pending-migration fixtures.
They remain explicitly opted in because default legacy deployments must not
activate a partially provisioned WEB runtime. Re-running checks the recorded
checksum. Rollback retains these additive ledgers and pinned execution ownership;
never drop them or reactivate legacy claims for worker-owned Turns.

094 is the HR v6 one-time product replacement. Before applying it, stop new HR
intake and reconcile all original in-flight executions. It preserves confirmed
standards and historical ledgers, replaces conversation binding gates with turn
scope, installs scoped tool receipts and revokes legacy task/package producers.
All three repositories and the fixed role/knowledge release must be ready before
reopening HR. v5 frozen commands must never be rewritten as v6.
