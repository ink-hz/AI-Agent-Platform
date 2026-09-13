# 106 migration source floor and scoped migration inventory

The host helper now requires the root 106 file while constructing its source checksum inventory and includes 106 in HR root-ledger prerequisites. Root-only execution continues to exclude independent HR-agent migrations and preview. No production invocation occurred.

The missing 106 negative failed before the helper change and passed afterward. The initial unit test name overstated its coverage: it directly calls root_checksums, whereas full Supervisor.validate performs read-only membership/session database checks earlier. It is renamed test_root_checksum_inventory_rejects_missing_attachment_106. This verifies inventory rejection before migration/grants; it does not claim all database/container I/O is absent from the full entrypoint. Old test names and logs remain unchanged.

Focused real local PostgreSQL/helper regression: 39 passed in 78.65s (runs/migration-106-focused-1). The synthetic next root probe is 107 and the deliberately invalid excluded HR probe 108, avoiding the real 106 identity. Renamed single test: 1 passed in 0.08s (runs/migration-106-name-scope-1). Initial missing-file RED/GREEN evidence is retained in migration-106-floor-red-1/green-1. These counts are distinct runs, not an 85-test combined suite.

Full control-plane migration suite initially 44 passed / 2 failed, retained in production/attachment-write-fence-fix/known-failures. The static root 88-only inventory omitted the established independent scopes and two root 102 tables. Inventory now requires each exact root/hr_web/hr_agent set, global unique 1–106 membership, and both cutover tables. No migration SQL or other expected tables are removed. Full suite 46 passed in 2.31s (runs/control-migration-manifest-green-1).

Independent review: review/fence-runtime-a608970b includes exact four-file snapshots/diff and run hashes; no important host scope/permission/assertion weakening found. Runtime null-slot cleanup and actual streaming PUT failures are separate open issues; these host results do not close them or establish production readiness.
